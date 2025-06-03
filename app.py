from flask import Flask, request, jsonify
import os
import requests
import re
import time
from datetime import date
from dotenv import load_dotenv
from collections import deque
from openai import OpenAI
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.platypus import Table, TableStyle
import io

load_dotenv()
app = Flask(__name__)

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
AIRTABLE_TOKEN = os.getenv("AIRTABLE_TOKEN")

BASE_ID = "appOuWggbJxUAcFzF"
TABLE_NAME = "Table 1"

client = OpenAI(api_key=OPENAI_API_KEY)

event_cache = deque(maxlen=100)
event_timestamps = {}
EVENT_CACHE_TTL = 60  # 秒

# Airtableから全データ取得しPDFを生成する関数
def generate_pdf_from_airtable():
    url = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_NAME}"
    headers = {"Authorization": f"Bearer {AIRTABLE_TOKEN}"}
    response = requests.get(url, headers=headers).json()

    records = response.get("records", [])
    if not records:
        return None

    # 表ヘッダー
    data = [["管理番号", "道具名", "使用者", "現在の場所", "ステータス", "最終更新日", "備考"]]
    for rec in records:
        f = rec.get("fields", {})
        data.append([
            f.get("管理番号", ""),
            f.get("道具名", ""),
            f.get("使用者", ""),
            f.get("現在の場所", ""),
            f.get("ステータス", ""),
            f.get("最終更新日", ""),
            f.get("備考", "")
        ])

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))

    table.wrapOn(c, width, height)
    table.drawOn(c, 30, height - 30 - 20 * len(data))  # 上から描画

    c.save()
    buffer.seek(0)
    return buffer

def upload_pdf_to_slack(channel_id):
    pdf_buffer = generate_pdf_from_airtable()
    if not pdf_buffer:
        return

    response = requests.post(
        "https://slack.com/api/files.upload",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
        files={"file": ("tool_list.pdf", pdf_buffer, "application/pdf")},
        data={
            "filename": "tool_list.pdf",
            "channels": channel_id,
            "initial_comment": "📄 最新の道具管理表を添付しました。"
        }
    )
    print("SlackへのPDF送信:", response.status_code, response.text)

def extract_tool_name(text):
    for word in ["の場所", "どこにありますか", "どこ", "場所", "は？", "は", "？"]:
        text = text.replace(word, "")
    return text.replace("　", " ").strip()

def find_tool_location(tool_name):
    url = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_NAME}"
    headers = {"Authorization": f"Bearer {AIRTABLE_TOKEN}"}
    if re.match(r"管理番号\s*\d+", tool_name):
        code = re.findall(r"\d+", tool_name)[0]
        formula = f"FIND('{code}', {{管理番号}})"
    else:
        formula = f"SEARCH(LOWER('{tool_name}'), LOWER({{道具名}}))"
    params = {"filterByFormula": formula}
    response = requests.get(url, headers=headers, params=params).json()
    if "records" in response and response["records"]:
        f = response["records"][0]["fields"]
        return f"{f.get('道具名')} は現在「{f.get('現在の場所')}」にあります。"
    else:
        return f"{tool_name} は見つかりませんでした。"

def update_user_and_location(message):
    lines = message.strip().split("\n")
    joined = " ".join(lines)
    update_items = []
    old_user = new_user = ""

    for line in lines:
        m = re.search(r"(.+?)を(.+?)から(.+?)へ", line)
        if m:
            update_items.append(m.group(1).strip())
            old_user = m.group(2).strip()
            new_user = m.group(3).strip()
        elif "から" in line and "へ" in line:
            m = re.search(r"(.+?)から(.+?)へ", line)
            if m:
                old_user = m.group(1).strip()
                new_user = m.group(2).strip()
        elif line.strip():
            update_items.append(line.strip())

    if not old_user or not new_user:
        return "変更対象の使用者や場所が読み取れませんでした。"

    headers = {"Authorization": f"Bearer {AIRTABLE_TOKEN}", "Content-Type": "application/json"}
    today = date.today().isoformat()
    success = 0
    failures = []

    for tool_line in update_items:
        url = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_NAME}"
        formula = f"SEARCH(LOWER('{tool_line}'), LOWER({{道具名}}))"
        params = {"filterByFormula": formula}
        response = requests.get(url, headers=headers, params=params).json()

        if "records" in response and response["records"]:
            record_id = response["records"][0]["id"]
            patch_url = f"{url}/{record_id}"
            update_data = {
                "fields": {
                    "使用者": new_user,
                    "現在の場所": new_user,
                    "最終更新日": today
                }
            }
            patch = requests.patch(patch_url, headers=headers, json=update_data)
            if patch.status_code == 200:
                success += 1
            else:
                failures.append(tool_line)
        else:
            failures.append(tool_line)

    msg = f"{success}件の道具情報を「{old_user}」から「{new_user}」へ更新しました。"
    if failures:
        msg += f"\n更新失敗：{', '.join(failures)}"
    return msg

@app.route('/slack', methods=['POST'])
def slack_events():
    data = request.get_json(force=True, silent=True)
    print("=== Slackから受信した生データ ===")
    print(data)

    if not data:
        return "NO DATA", 400

    event_id = data.get("event_id")
    now = time.time()

    if event_id in event_timestamps and now - event_timestamps[event_id] < EVENT_CACHE_TTL:
        print(f"⚠️ 重複イベント {event_id} をスキップ")
        return "Duplicate", 200

    if data.get("type") == "url_verification":
        return jsonify({"challenge": data["challenge"]})

    if data.get("type") == "event_callback":
        event = data["event"]
        if event.get("type") == "app_mention":
            raw_text = event.get("text", "")
            channel_id = event.get("channel")
            cleaned_text = re.sub(r"<@[\w]+>", "", raw_text).strip()
            print("ユーザーからのメッセージ:", cleaned_text)

            if "から" in cleaned_text and "へ" in cleaned_text:
                reply_text = update_user_and_location(cleaned_text)
                # PDFをSlackにアップロード
                upload_pdf_to_slack(channel_id)
            elif "どこ" in cleaned_text or "場所" in cleaned_text:
                tool_name = extract_tool_name(cleaned_text)
                reply_text = find_tool_location(tool_name)
            elif "/pdf" in cleaned_text or "一覧" in cleaned_text:
                reply_text = "📄 道具管理表を生成しました。"
                upload_pdf_to_slack(channel_id)
            else:
                response = client.chat.completions.create(
                    model="gpt-4-turbo",
                    messages=[
                        {"role": "system", "content": "あなたはSlack上の親切なアシスタントBotです。"},
                        {"role": "user", "content": cleaned_text}
                    ]
                )
                reply_text = response.choices[0].message.content.strip()

            requests.post("https://slack.com/api/chat.postMessage", json={
                "channel": channel_id,
                "text": reply_text
            }, headers={
                "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
                "Content-type": "application/json"
            })

            event_timestamps[event_id] = now
            event_cache.append(event_id)

    return "OK", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
