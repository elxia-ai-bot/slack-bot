from flask import Flask, request, jsonify
import os
import requests
import re
import time
from datetime import date
from dotenv import load_dotenv
from collections import deque
from openai import OpenAI

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

def extract_tool_name(text):
    keywords_to_remove = ["の場所", "どこにありますか", "どこ", "場所", "は？", "は", "？"]
    for word in keywords_to_remove:
        text = text.replace(word, "")
    text = text.replace("　", " ")
    return text.strip()

def find_tool_location(tool_name):
    url = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_NAME}"
    headers = {"Authorization": f"Bearer {AIRTABLE_TOKEN}"}
    match = re.search(r"管理番号\s*(\d+)", tool_name)
    if match:
        code = match.group(1)
        formula = f"FIND('{code}', {{管理番号}})"
    else:
        tool_name = tool_name.replace("　", " ").strip()
        formula = f"SEARCH(LOWER('{tool_name}'), LOWER({{道具名}}))"

    params = {"filterByFormula": formula}
    response = requests.get(url, headers=headers, params=params).json()

    if "records" in response and response["records"]:
        record = response["records"][0]["fields"]
        return f"{record.get('道具名')} は現在「{record.get('現在の場所')}」にあります。"
    else:
        return f"{tool_name} は見つかりませんでした。"

def get_tool_list_by_user(user_name):
    url = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_NAME}"
    headers = {"Authorization": f"Bearer {AIRTABLE_TOKEN}"}
    formula = f"{{使用者}} = '{user_name}'"
    params = {"filterByFormula": formula}
    response = requests.get(url, headers=headers, params=params).json()

    if "records" in response and response["records"]:
        lines = [f"・{rec['fields'].get('管理番号', '不明')}：{rec['fields'].get('道具名', '名称なし')}" for rec in response["records"]]
        return f"\n🧰 現在 {user_name} さんが使用している道具一覧:\n" + "\n".join(lines)
    else:
        return f"\n🧰 現在 {user_name} さんが使用している道具はありません。"

def update_user_and_location(message):
    lines = message.strip().split("\n")
    joined = " ".join(lines)
    update_items = []
    old_user = new_user = ""

    # 複数パターンに対応
    for line in lines:
        # パターン1: 「道具名をAからBへ」
        m = re.search(r"(.+?)を(.+?)から(.+?)へ", line)
        if m:
            tool_name = m.group(1).strip()
            old_user = m.group(2).strip()
            new_user = m.group(3).strip()
            update_items.append(tool_name)
            continue

        # パターン2: 「AからBへ」だけを含む行
        m2 = re.search(r"(.+?)から(.+?)へ", line)
        if m2:
            old_user = m2.group(1).strip()
            new_user = m2.group(2).strip()
            continue

        # パターン3: 上記に該当しない → 道具名の行として追加
        if line.strip():
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
            update_data = {
                "fields": {
                    "使用者": new_user,
                    "現在の場所": new_user,
                    "最終更新日": today
                }
            }
            patch_url = f"{url}/{record_id}"
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
    msg += get_tool_list_by_user(new_user)
    return msg

@app.route('/slack', methods=['POST'])
def slack_events():
    data = request.get_json(force=True, silent=True)
    print("=== Slackから受信した生データ ===")
    print(data)

    if data is None:
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
            elif "どこ" in cleaned_text or "場所" in cleaned_text:
                tool_name = extract_tool_name(cleaned_text)
                reply_text = find_tool_location(tool_name)
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
