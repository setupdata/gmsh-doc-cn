---
status: accepted
---

# 将 PO 译文与 AI 审校来源分开保存

`po/zh_CN.po` 只保存翻译所需的稳定上下文、原文、译文和标准状态标志。模型配置、提示词版本、原文哈希、各轮审校结论和问题按批次写入 `reviews/<version>/<batch>.jsonl`；总体状态由构建生成到 `reports/status.json`，不得手工维护。这样既能追溯 AI 译文来源，又避免运行细节造成 PO 文件的大量无关变化。
