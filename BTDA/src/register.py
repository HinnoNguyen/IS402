import os
import argparse

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model_dir", type=str, required=True)
    parser.add_argument("--eval_result", type=str, required=True)
    args = parser.parse_args()

    # 1. Đọc tên mô hình tốt nhất từ khối đánh giá
    result_path = os.path.join(args.eval_result, "result.txt")
    with open(result_path, "r") as f:
        best_model_name = f.read().strip()
        
    print(f"Đã xác nhận Quality Gate cho mô hình: {best_model_name}")

    # 2. Định vị file mô hình chính xác
    model_path = os.path.join(args.model_dir, best_model_name, "pipeline.joblib")
    print(f"Chuẩn bị đăng ký file: {model_path} lên Azure Model Catalog...")

if __name__ == "__main__":
    main()