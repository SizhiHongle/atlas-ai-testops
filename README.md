# Atlas AI 测试平台交付包 v0.2

## 目录

- `documents/Atlas_AI用例与浏览器Agent工作流功能设计及实现对齐稿_v0.2.docx`
  - 用例、Case-owned 编排画布、浏览器 Agent、ATMP、运行冻结边界及技术实现基线。
- `frontend/atlas-ai-testops-prototype/`
  - 当前 Atlas AI 测试平台前端原型源码，包含主工作台与登录页。

## 本地运行前端

环境要求：Node.js `>= 22.13.0`。

```bash
cd frontend/atlas-ai-testops-prototype
npm ci
npm run dev
```

代码检查与生产构建：

```bash
npm run lint
npm run build
```

## 说明

- 交付包未包含 `node_modules`、`dist`、`.wrangler`、Git 历史和本地缓存。
- 当前前端属于可交互产品原型，业务数据与执行过程以演示数据为主。
- 打包前已通过 TypeScript 检查与生产构建。
