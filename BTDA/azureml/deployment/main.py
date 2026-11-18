"""Scoring script cho Azure ML managed online endpoint (credit-intent-endpoint).

Model đăng ký là MLflow pyfunc (`IntentClassifier` trong src/intent_model.py), nhận DataFrame cột `text`,
trả về `label` + `confidence`. Môi trường chạy phải khớp phiên bản lúc train (xem conda.yaml).

Request hỗ trợ:
  {"data": ["câu 1", "câu 2"]}
  {"data": {"columns": ["text"], "data": [["câu 1"]]}}
  {"input_data": {"columns": ["text"], "data": [["câu 1"]]}}   # định dạng tab Test của Azure ML Studio
"""
import json
import logging
import os

import mlflow
import pandas as pd

model = None


def init():
    global model
    base_path = os.getenv("AZUREML_MODEL_DIR")
    if not base_path:
        raise RuntimeError("AZUREML_MODEL_DIR is not set")

    # Model đăng ký từ job MLflow nằm trong thư mục con `model/`
    model_path = next(
        (
            candidate
            for candidate in (base_path, os.path.join(base_path, "model"))
            if os.path.isfile(os.path.join(candidate, "MLmodel"))
        ),
        None,
    )
    if model_path is None:
        raise FileNotFoundError(
            f"Could not find MLmodel in {base_path!r} or its 'model' subdirectory"
        )

    model = mlflow.pyfunc.load_model(model_path)
    logging.info("Model loaded successfully from %s", model_path)


def _to_dataframe(data):
    if isinstance(data, str):
        return pd.DataFrame({"text": [data]})
    if isinstance(data, dict):
        if {"columns", "data"}.issubset(data):
            return pd.DataFrame(data["data"], columns=data["columns"], index=data.get("index"))
        return pd.DataFrame(data)
    if isinstance(data, list):
        if not data or isinstance(data[0], str):
            return pd.DataFrame({"text": data})
        return pd.DataFrame(data)
    raise ValueError("'data' must be a string, JSON object or array")


def run(raw_data):
    try:
        payload = json.loads(raw_data)
        data = payload.get("input_data", payload.get("data"))
        if data is None:
            raise ValueError("Request JSON must contain a 'data' or 'input_data' field")

        model_input = _to_dataframe(data)
        if "text" not in model_input.columns:
            raise ValueError("Input must contain a 'text' column")

        predictions = model.predict(model_input)
        return {"predictions": predictions.to_dict(orient="records")}
    except Exception as ex:  # trả lỗi rõ ràng thay vì HTTP 502 khó debug
        logging.exception("Scoring failed")
        return {"error": str(ex)}