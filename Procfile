# 既有股市分析 app（Railway 預設進入點）—— 刻意保持不變，確保現有部署零回歸。
web: python app.py

# Scott Studio（AI 影片／短劇生成平台）。
# 需先安裝 requirements-studio.txt；此進入點同時服務 Studio API 與上面的 Flask app。
# Railway 若要改用 Studio，將 railway.json 的 startCommand 指向這個指令。
studio: uvicorn studio_server:application --host 0.0.0.0 --port ${PORT:-8000}

# Studio 背景任務 worker（STUDIO_TASK_EXECUTION_MODE=celery 時需要）。
worker: celery -A studio.tasks.celery_app:celery_app worker -l info
