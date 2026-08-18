---
status: accepted
---

# PO 是唯一可编辑译文来源

所有翻译单元保存在 `po/zh_CN.po`，中文 Texinfo 和 HTML 仅由构建生成，不接受手工修改。项目先验证 po4a 的 Texinfo 解析器；如果它不能无损处理 Gmsh 手册，就用专用抽取和回填程序替换解析器，继续生成相同的 POT 和 PO。这样，上游合并、AI 翻译、术语检查和审校流程不依赖某个 Texinfo 解析器。
