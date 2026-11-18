import os
import json
import argparse

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model_dir", type=str, required=True)
    parser.add_argument("--data_dir", type=str, required=True)
    parser.add_argument("--eval_output", type=str, required=True)
    args = parser.parse_args()

    # 1. Đọc file cấu hình từ bước train
    best_model_json = os.path.join(args.model_dir, "best_model.json")
    with open(best_model_json, "r") as f:
        best_info = json.load(f)

    best_model_name = best_info["best_model"]
    f1_score = best_info["f1_macro"]
    
    print(f"Mô hình xuất sắc nhất được chọn: {best_model_name}")
    print(f"F1 Macro: {f1_score}")

    # 2. Cổng kiểm soát chất lượng (Quality Gate)
    if f1_score < 0.85:
        raise ValueError(f"Quality Gate Failed! F1 Score {f1_score} < 0.85.")
    
    print("Quality Gate Passed! Đạt chuẩn.")
    
    # 3. Ghi tên mô hình tốt nhất vào log để chuyển cho bước Register
    os.makedirs(args.eval_output, exist_ok=True)
    with open(os.path.join(args.eval_output, "result.txt"), "w") as f:
        f.write(best_model_name)

if __name__ == "__main__":
    main()