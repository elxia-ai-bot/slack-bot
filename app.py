from flask import Flask, request, jsonify
import os
import requests
import re
from datetime import date
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
app = Flask(__name__)

# 環境変数読み込み
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
AIRTABLE_TOKEN = os.getenv("AIRTABLE_TOKEN")

# Airtable設定
BASE_ID = "appOuWggbJxUAcFzF"
TABLE_NAME = "Table 1"  # 必要に応じて"道具一覧"に戻してください

client = OpenAI(api_key=OPENAI_API_KEY)

recent_event_ids = set()

def extract_tool_name(text):
    keywords_to_remove = ["の場所", "どこ", "場所", "は？", "は", "？"]
    for word in keywords_to_remove:
        text = text.replace(word, "")
    text = text.replace("　", " ")  # 全角スペース→半角
    return text.strip()

# 使用者・場所の更新処理
def update_user_and_location(message):
    lines = message.strip().split("\n")
    record_lines = [line for line in lines if re.match(r"^\d+\.", line)]
    movement_line = next((line for line in lines if "から" in line and "へ" in line), "")

    if not record_lines or not movement_line:
        return "変更指示が正しく読み取れませんでした。"

    old_user, new_user = re.findall(r"(.*?)から(.*?)へ", movement_line)[0]

    headers = {
        "Authorization": f"Bearer {AIRTABLE_TOKEN}",
        "Content-Type": "application/json"
    }

    today = date.today().isoformat()
    success = 0
    failures = []

    for line in record_lines:
        match = re.match(r"(\d+)\.(.+)", line.strip())
        if not match:
            continue
        code, name = match.groups()
        tool_code = code.strip()

        # Airtable検索
        url = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_NAME}"
        formula = f"FIND('{tool_code}', {{管理番号}})"
        params = {"filterByFormula": formula}
        response = requests.get(url, headers=headers, params=params).json()

        if "records" in response and response["records"]:
            record_id = response["records"][0]["id"]

            update_data = {
                "fields": {
                    "使用者": new_user.strip(),
                    "現在の場所": new_user.strip(),
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

            if "変更をお願いします" in cleaned_text:
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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
