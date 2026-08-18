# Gmsh 中文文档

这是 [Gmsh 4.15.2 参考手册](https://gmsh.info/doc/texinfo/gmsh.html)的非官方简体中文翻译项目。项目从经过 SHA-256 校验的官方发布包抽取 Texinfo 内容，把 `po/zh_CN.po` 作为可发布译文的唯一编辑来源。当前已完成整份手册的简体中文翻译、独立审校和确定性检查，并实现可复现的英文静态构建和非正式中文预览构建；正式网站发布仍属于后续任务。

当前仓库包含 6916 个正式译文单元和 100 单元模型质量基线，还没有公开发布中文手册。基准参考译文已经经过相互隔离的中文审校、技术审校和确定性检查，最终问题数均为零。正文采用同样的初译、独立中文审校、独立技术审校、定向修订和受保护内容检查流程。结构化审校记录是从各次 Codex 任务导入的只读证据；基线命令只校验记录及其哈希，不会重新生成审校结论。

## 已完成的基础能力

- 固定 Gmsh `4.15.2`、标签、提交、源码包地址和 SHA-256；
- 保存上游 GPL v2-or-later 许可证及 Gmsh 特别链接例外，并明确 MIT/GPL 文件边界；
- 在固定容器中生成拆分页和单页英文 HTML，并对两次空目录构建逐文件比较 SHA-256；
- 使用本项目的 Texinfo 子集解析器生成 POT/PO，保护节点、锚点、交叉引用、代码、API、选项、数值和外置文件；
- 把 `CHANGELOG.txt` 和 `CREDITS.txt` 中允许翻译的自然语言纳入同一 PO，排除姓名、许可证和排版结构；
- 实际试跑 po4a 0.74 后，因其改写交叉引用和受保护的 Texinfo 命令而不予采用；项目自带的兼容性试译覆盖 Overview、t1–t3 和 10 个 API 条目，无译文往返保持源文件字节与结构不变；
- 建立并经两个独立 AI 会话审查通过的 150 条术语表；
- 建立 100 单元分层基准集，完成初译、两类独立审校、最多两轮定向修订和逐单元受保护内容检查，并将初译、最终参考译文和逐条审校证据的哈希冻结在仓库中；
- 完成全部 6916 个翻译单元，并由 PO、独立审校记录和自动检查确定性归并到 `formal` 状态；
- 从同一上游快照生成英文拆分页、英文单页、中文预览拆分页和中文预览单页；预览只应用当前状态为 `formal` 的译文，如有非正式单元则回退到固定上游英文，并在相应页面显示“英文回退”说明、`data-translation-state` 和 `noindex,nofollow`。

## 本地验证

本地可以直接运行不依赖 Texinfo 的测试：

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -v
```

权威检查使用固定镜像摘要和 Debian 软件包快照：

```bash
docker build --file container/Containerfile --tag gmsh-doc-cn:check .
docker run --rm --volume "$PWD:/workspace" gmsh-doc-cn:check fetch
docker run --rm --volume "$PWD:/workspace" gmsh-doc-cn:check baseline \
  --source upstream/cache/source/gmsh-4.15.2-source --output build/generated
docker run --rm --volume "$PWD:/workspace" gmsh-doc-cn:check reproducible-english \
  --source upstream/cache/source/gmsh-4.15.2-source \
  --output reports/english-artifacts.json

# 先生成全量状态报告，再构建双语预览：
docker run --rm --volume "$PWD:/workspace" gmsh-doc-cn:check status \
  --source upstream/cache/source/gmsh-4.15.2-source \
  --check-all \
  --checks-output reports/deterministic-checks.json \
  --output reports/status.json

docker run --rm --volume "$PWD:/workspace" gmsh-doc-cn:check reproducible-preview \
  --source upstream/cache/source/gmsh-4.15.2-source \
  --dist build/preview-site \
  --status reports/status.json \
  --check-all \
  --output reports/preview-artifacts.json
```

缓存和构建目录不会提交。状态及预览命令会重新读取审校批次、对全部单元执行确定性检查并归并状态；预览重新计算的结果必须与 `reports/status.json` 完全一致，不能通过手工修改状态文件把译文放入预览。GitHub Actions 会重新下载并校验上游包、运行测试、重建确定性基线，并分别比较两次英文构建和两次双语预览构建。中文预览作为工作流产物保存，不会自动部署到公开地址。

## 关键文件

- `upstream/manifest.toml`：固定上游来源；
- `docs/design/translation-plan.md`：已确认的完整设计；
- `po/gmsh.pot`、`po/zh_CN.po`：翻译单元和中文译文；
- `glossary/terms.csv`：已审查术语表；
- `benchmarks/qualification-v1.json`：100 单元模型质量基线的资格报告；
- `tests/fixtures/pilot/pilot-report.json`：兼容性往返证据；
- `NOTICE.md`、`docs/license-matrix.md`：来源和许可说明。

根目录 `LICENSE` 只适用于本项目自行编写且未混入上游正文或译文的 MIT 代码和样式。上游内容、中文翻译和相关产物的具体许可边界见 `NOTICE.md` 与许可矩阵。
