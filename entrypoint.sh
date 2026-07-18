#!/bin/sh
echo "Starting KaGaMa backend..."
echo "PORT=$PORT"
python -c "import flask; print(f'Flask {flask.__version__}')"
python -c "import flask_sqlalchemy; print('flask_sqlalchemy OK')"
python -c "import flask_login; print('flask_login OK')"
python -c "import flask_limiter; print('flask_limiter OK')"
python -c "import flask_mail; print('flask_mail OK')"
python -c "import gunicorn; print(f'gunicorn {gunicorn.__version__}')"
echo "All imports OK, starting gunicorn..."
exec gunicorn flask_app:app --bind 0.0.0.0:${PORT:-8000} --timeout 120 --log-level info
