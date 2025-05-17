import requests
import os
from dotenv import load_dotenv

# .envファイルからトークンを読み込む
load_dotenv()
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")

print("読み込んだトークン:", SLACK_BOT_TOKEN)

# Slackに送る情報
url = "https://slack.com/api/chat.postMessage"
headers = {
    "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
    "Content-type": "application/json"
}
data = {
    "channel": "#general",  # チャンネル名（例: #general）またはチャンネルIDに変更可
    "text": "こんにちは！Botからのメッセージです。"
}

# リクエスト送信
response = requests.post(url, json=data, headers=headers)

# 結果を表示
print(response.status_code)
print(response.json())