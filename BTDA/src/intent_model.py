"""MLflow pyfunc wrapper: accepts a DataFrame with a `text` column, returns label names + confidence.
Shipped with the model via `code_paths`, so the Azure ML endpoint can load it without extra scoring code."""
import json
import re

import joblib
import mlflow.pyfunc
import numpy as np
import pandas as pd


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).lower().strip())


class IntentClassifier(mlflow.pyfunc.PythonModel):
    def load_context(self, context):
        self.pipeline = joblib.load(context.artifacts["pipeline"])
        with open(context.artifacts["labels"], encoding="utf-8") as f:
            self.labels = json.load(f)["labels"]

    def predict(self, context, model_input, params=None):
        texts = model_input["text"] if isinstance(model_input, pd.DataFrame) else model_input
        texts = [clean_text(t) for t in texts]
        idx = self.pipeline.predict(texts)
        clf = self.pipeline[-1]
        if hasattr(clf, "predict_proba"):
            confidence = self.pipeline.predict_proba(texts).max(axis=1)
        else:  # LinearSVC: softmax over decision scores as a rough confidence
            scores = self.pipeline.decision_function(texts)
            exp = np.exp(scores - scores.max(axis=1, keepdims=True))
            confidence = (exp / exp.sum(axis=1, keepdims=True)).max(axis=1)
        return pd.DataFrame({"label": [self.labels[i] for i in idx],
                             "confidence": np.round(confidence, 4)})
