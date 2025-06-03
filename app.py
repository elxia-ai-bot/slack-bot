from flask import Flask, request, jsonify
import os
import requests
import re
from datetime import date
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
app = Flask(__name__)

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
AIRTABLE_TOKEN = os.getenv("AIRTABLE_TOKEN")

BASE_ID = "appOuWggbJxUAcFzF"
TABLE_NAME = "Table 1"  # 必要に応じて変更

client = OpenAI(api_key=OPENAI_API_KEY)

recent_event_ids = set()

def extract_tool_name(text):
    keywords_to_remove = ["の場所", "どこ", "場所", "は？", "は", "？"]
    for word in keywords_to_remove:
        text = text.replace(word, "")
    text = text.replace("　", " ")  # 全角→半角
    return text.strip()

def get_tool_list_by_user(user_name):
    url = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_NAME}"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_TOKEN}",
        "Content-Type": "application/json"
    }
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

    # 全文から「〇〇から△△へ」を正しく抜き出す
    joined = " ".join(lines)
    match = re.search(r"(.+?)から(.+?)へ", joined)
    if not match:
        return "変更対象の使用者や場所が読み取れませんでした。"
    old_user, new_user = match.group(1).strip(), match.group(2).strip()

    # 管理番号を含む行のみを処理対象に
    record_lines = [line for line in lines if re.search(r"\d+", line)]

    if not record_lines:
        return "変更対象の道具が見つかりませんでした。"

    headers = {
        "Authorization": f"Bearer {AIRTABLE_TOKEN}",
        "Content-Type": "application/json"
    }

    today = date.today().isoformat()
    success = 0
    failures = []

    for line in record_lines:
        match = re.search(r"(\d+)", line)
        if not match:
            continue
        tool_code = match.group(1).strip()

        url = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_NAME}"
        formula = f"FIND('{tool_code}', {{管理番号}})"
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
                failures.append(tool_code)
        else:
            failures.append(tool_code)

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

    if data.get("type") == "url_verification":
        return jsonify({"challenge": data["challenge"]})

    if data.get("type") == "event_callback":
        event_id = data.get("event_id")
        if event_id in recent_event_ids:
            print(f"⚠️ 重複イベント {event_id} をスキップ")
            return "Duplicate", 200
        recent_event_ids.add(event_id)

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

            slack_response = requests.post("https://slack.com/api/chat.postMessage", json={
                "channel": channel_id,
                "text": reply_text
            }, headers={
                "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
                "Content-type": "application/json"
            })

            print("Slackへの送信結果:", slack_response.status_code, slack_response.text)

    return "OK", 200
