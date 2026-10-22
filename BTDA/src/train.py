"""MLOps inner loop: train several text classifiers, log Accuracy / F1 / Confusion Matrix to MLflow,
and keep the best model (quality gate: macro F1 > threshold)."""
import argparse
import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import joblib
import matplotlib.pyplot as plt
import mlflow
import mlflow.pyfunc
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (ConfusionMatrixDisplay, accuracy_score,
                             classification_report, f1_score)
from sklearn.pipeline import FeatureUnion, Pipeline
from sklearn.preprocessing import LabelEncoder
from sklearn.svm import LinearSVC
from xgboost import XGBClassifier

from intent_model import IntentClassifier


def tfidf_features():
    return FeatureUnion([
        ("word", TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, min_df=1)),
        ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), sublinear_tf=True, min_df=2)),
    ])


def candidate_models():
    return {
        "tfidf_logreg": LogisticRegression(C=10, max_iter=3000, class_weight="balanced"),
        "tfidf_linearsvc": LinearSVC(C=1.0, class_weight="balanced"),
        "tfidf_xgboost": XGBClassifier(n_estimators=400, max_depth=6, learning_rate=0.2,
                                       tree_method="hist", n_jobs=-1),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data_dir", default="data/processed")
    parser.add_argument("--out_dir", default="outputs")
    parser.add_argument("--experiment", default="banking-intent-classification")
    parser.add_argument("--f1_threshold", type=float, default=0.85)
    parser.add_argument("--mapping", default="config/intent_mapping.json")
    args = parser.parse_args()

    data_dir, out_dir = Path(args.data_dir), Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    train = pd.read_csv(data_dir / "train.csv")
    test = pd.read_csv(data_dir / "test.csv")

    # Data lineage: record which mapping version produced these labels
    mapping_path = Path(args.mapping)
    mapping_bytes = mapping_path.read_bytes()
    mapping_version = json.loads(mapping_bytes).get("version", "unknown")
    mapping_md5 = hashlib.md5(mapping_bytes).hexdigest()
    label_counts = train["label"].value_counts().to_dict()

    encoder = LabelEncoder().fit(train["label"])
    y_train, y_test = encoder.transform(train["label"]), encoder.transform(test["label"])
    labels = list(encoder.classes_)

    mlflow.set_experiment(args.experiment)
    results = {}

    for name, clf in candidate_models().items():
        with mlflow.start_run(run_name=name) as run:
            model = Pipeline([("features", tfidf_features()), ("clf", clf)])
            model.fit(train["text"], y_train)
            pred = model.predict(test["text"])

            metrics = {
                "accuracy": accuracy_score(y_test, pred),
                "f1_macro": f1_score(y_test, pred, average="macro"),
                "f1_weighted": f1_score(y_test, pred, average="weighted"),
            }
            mlflow.log_params({"model": name, **{f"clf__{k}": v for k, v in clf.get_params().items()
                                                 if isinstance(v, (int, float, str, bool))}})
            mlflow.log_metrics(metrics)
            mlflow.set_tags({"mapping_version": mapping_version, "mapping_md5": mapping_md5})
            mlflow.log_params({f"n_train_{k}": v for k, v in label_counts.items()})
            mlflow.log_artifact(str(mapping_path), artifact_path="data_config")

            fig, ax = plt.subplots(figsize=(7, 6))
            ConfusionMatrixDisplay.from_predictions(y_test, pred, display_labels=labels,
                                                    xticks_rotation=45, ax=ax, cmap="Blues")
            ax.set_title(f"Confusion Matrix - {name}")
            fig.tight_layout()
            mlflow.log_figure(fig, "confusion_matrix.png")
            fig.savefig(out_dir / f"confusion_matrix_{name}.png")
            plt.close(fig)

            report = classification_report(y_test, pred, target_names=labels)
            mlflow.log_text(report, "classification_report.txt")

            # Package pipeline + label names as a pyfunc that returns label strings
            model_dir = out_dir / name
            model_dir.mkdir(exist_ok=True)
            joblib.dump(model, model_dir / "pipeline.joblib")
            (model_dir / "labels.json").write_text(json.dumps({"labels": labels}))
            example = pd.DataFrame({"text": test["text"].head(3).tolist()})
            wrapper = IntentClassifier()
            mlflow.pyfunc.log_model(
                name="model",
                python_model=wrapper,
                artifacts={"pipeline": str(model_dir / "pipeline.joblib"),
                           "labels": str(model_dir / "labels.json")},
                code_paths=[str(Path(__file__).parent / "intent_model.py")],
                input_example=example,
                pip_requirements=["mlflow", "scikit-learn==1.9.1", "xgboost==3.4.1", "pandas", "joblib"],
            )

            results[name] = {**metrics, "run_id": run.info.run_id}
            print(f"\n=== {name} ===\n{json.dumps(metrics, indent=2)}\n{report}")

    best_name = max(results, key=lambda n: results[n]["f1_macro"])
    best = results[best_name]
    passed = best["f1_macro"] > args.f1_threshold
    summary = {"best_model": best_name, **best, "labels": labels, "mapping_version": mapping_version,
               "f1_threshold": args.f1_threshold, "quality_gate_passed": passed}
    (out_dir / "best_model.json").write_text(json.dumps(summary, indent=2))
    print(f"\nBest: {best_name} | F1 macro = {best['f1_macro']:.4f} | Quality gate passed: {passed}")
    if not passed:
        raise SystemExit("Quality gate FAILED: F1 below threshold")


if __name__ == "__main__":
    main()
