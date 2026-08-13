---
status: accepted
---

# 在 GitHub Pages 使用稳定的版本化地址

首期网站发布到 `https://setupdata.github.io/gmsh-doc-cn/`，并把 `/gmsh-doc-cn/` 作为固定的 `SITE_BASE`。正式版位于 `${SITE_BASE}v<gmsh-version>/zh-cn/`，预览版位于 `${SITE_BASE}preview/v<gmsh-version>/zh-cn/`，`${SITE_BASE}latest/zh-cn/` 只通过静态重定向指向最新正式版。构建、资源、语言切换和搜索链接都必须注入 `SITE_BASE`，不得假定部署在域名根目录；以后采用自定义域名时保留版本路径，并为原项目路径提供重定向。
