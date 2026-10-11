FROM python:3.12-slim
RUN pip install --no-cache-dir jsonschema==4.23.0
WORKDIR /app
COPY app.py index.html ./
ENV HOST=0.0.0.0 PORT=8877 VISITORPLAN_DB=/data/visitorplan.sqlite3
VOLUME /data
EXPOSE 8877
CMD ["python","-B","app.py"]
