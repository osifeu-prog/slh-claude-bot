FROM python:3.11-slim
WORKDIR /app
COPY slh-claude-bot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY slh-claude-bot/ .
CMD ["python", "bot.py"]
