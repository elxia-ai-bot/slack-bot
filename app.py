from flask import Flask, request, jsonify
import os
import requests
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
app = Flask(__name__)

# 環境変数読み込み
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
AIRTABLE_TOKEN = os.getenv("AIRTABLE_TOKEN")

# Airtable情報
BASE_ID = "appOuWggbJxUAcFzF"
TABLE_NAME = "道具一覧"

client = OpenAI(api_key=OPENAI_API_KEY)

# イベントの重複検知（メモリ内保存）
recent_event_ids = set()

def find_tool_location(tool_name):
    url = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_NAME}"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_TOKEN}",
        "Content-Type": "application/json"
    }
    params = {
        "filterByFormula": f"FIND('{tool_name}', {{道具名}})"
    }
    response = requests.get(url, headers=headers, params=params)
    data = response.json()
    if "records" in data and len(data["records"]) > 0:
        record = data["records"][0]["fields"]
        return f"{record.get('道具名')} は現在「{record.get('現在の場所')}」にあります。"
    else:
        return f"{tool_name} は見つかりませんでした。"

@app.route('/slack', methods=['POST'])
def slack_events():
    data = request.get_json(force=True, silent=True)
    print("=== Slackから受信した生データ ===")
    print(data)

    if data is None:
        return "NO DATA", 400

    # チャレンジ応答
    if data.get("type") == "url_verification":
        return jsonify({"challenge": data["challenge"]})

    # イベント処理
    if data.get("type") == "event_callback":
        event_id = data.get("event_id")

        # ✅ 重複処理の防止
        if event_id in recent_event_ids:
            print(f"⚠️ 重複イベント {event_id} をスキップ")
            return "Duplicate", 200
        recent_event_ids.add(event_id)

        event = data["event"]
        if event.get("type") == "app_mention":
            raw_text = event.get("text", "")
            channel_id = event.get("channel")

            # ✅ メンションを除去したテキストを処理対象に
            cleaned_text = raw_text
            if raw_text.startswith("<@"):
                cleaned_text = " ".join(raw_text.split(" ")[1:]).strip()

            print("ユーザーからのメッセージ:", cleaned_text)

            # Airtable検索
            if "どこ" in cleaned_text or "場所" in cleaned_text:
                reply_text = find_tool_location(cleaned_text)
            else:
                response = client.chat.completions.create(
                    model="gpt-4",
                    messages=[
                        {"role": "system", "content": "あなたはSlack上の親切なアシスタントBotです。"},
                        {"role": "user", "content": cleaned_text}
                    ]
                )
                reply_text = response.choices[0].message.content.strip()

            # Slackへ返信
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
