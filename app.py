from flask import Flask, request, jsonify
import os
import requests
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

app = Flask(__name__)

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)

@app.route('/slack', methods=['POST'])
def slack_events():
    data = request.get_json(force=True, silent=True)
    print("=== Slackから受信した生データ ===")
    print(data)

    if data is None:
        return "NO DATA", 400

    # チャレンジ応答（Slackが最初に送ってくる確認用）
    if data.get("type") == "url_verification":
        return jsonify({"challenge": data["challenge"]})

    # イベント受信処理
    if data.get("type") == "event_callback":
        event = data["event"]
        if event.get("type") == "app_mention":
            user_text = event.get("text")
            channel_id = event.get("channel")

            print("ユーザーからのメッセージ:", user_text)

            # OpenAI に投げる
            response = client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "あなたはSlack上の親切なアシスタントBotです。"},
                    {"role": "user", "content": user_text}
                ]
            )

            reply_text = response.choices[0].message.content.strip()

            # Slackに返信
            slack_response = requests.post("https://slack.com/api/chat.postMessage", json={
                "channel": channel_id,
                "text": reply_text
            }, headers={
                "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
                "Content-type": "application/json"
            })

            # 👇 送信結果をログに出力（ここが超重要！）
            print("Slackへの送信結果:", slack_response.status_code, slack_response.text)

    return "OK", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
