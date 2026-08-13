# Gmsh 中文文档

这是 [Gmsh 4.15.2 参考手册](https://gmsh.info/doc/texinfo/gmsh.html)的非官方简体中文翻译项目。项目从经过 SHA-256 校验的官方发布包抽取 Texinfo 内容，把 `po/zh_CN.po` 作为可发布译文的唯一编辑来源。当前已经实现可复现的英文静态构建；中文网站构建和完整翻译属于后续任务。

当前仓库完成的是翻译基础设施和模型准入样本，还没有发布完整中文手册。现有 100 单元报告只证明抽样有效，状态为 `selection_qualified_pending_review`；不得把它解释为模型已经通过 100 单元质量评测。

## 已完成的基础能力

- 固定 Gmsh `4.15.2`、标签、提交、源码包地址和 SHA-256；
- 保存上游 GPL v2-or-later 许可证及 Gmsh 特别链接例外，并明确 MIT/GPL 文件边界；
- 在固定容器中生成拆分页和单页英文 HTML，并对两次空目录构建逐文件比较 SHA-256；
- 使用本项目的 Texinfo 子集解析器生成 POT/PO，保护节点、锚点、交叉引用、代码、API、选项、数值和外置文件；
- 把 `CHANGELOG.txt` 和 `CREDITS.txt` 中允许翻译的自然语言纳入同一 PO，排除姓名、许可证和排版结构；
- 实际试跑 po4a 0.74 后，因其改写交叉引用和受保护的 Texinfo 命令而不予采用；项目自带的兼容性试译覆盖 Overview、t1–t3 和 10 个 API 条目，无译文往返保持源文件字节与结构不变；
- 建立并经两个独立 AI 会话审查通过的 150 条术语表；
- 建立 100 单元分层基准集，以及已跑通翻译、两类独立审校、一次修订和确定性检查的 5 单元小样；
- 证明一个 PO 单元能够由审校记录确定性归并到 `formal` 状态。

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
```

缓存、构建目录和报告不会提交。GitHub Actions 会重新下载并校验上游包、运行测试、重建确定性基线，并比较两次英文构建。

## 关键文件

- `upstream/manifest.toml`：固定上游来源；
- `docs/design/translation-plan.md`：已确认的完整设计；
- `po/gmsh.pot`、`po/zh_CN.po`：翻译单元和中文译文；
- `glossary/terms.csv`：已审查术语表；
- `benchmarks/qualification-v1.json`：100 单元抽样资格报告；
- `tests/fixtures/pilot/pilot-report.json`：兼容性往返证据；
- `NOTICE.md`、`docs/license-matrix.md`：来源和许可说明。

根目录 `LICENSE` 只适用于本项目自行编写且未混入上游正文或译文的 MIT 代码和样式。上游内容、中文翻译和相关产物的具体许可边界见 `NOTICE.md` 与许可矩阵。
