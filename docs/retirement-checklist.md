# StoryForge2 退役清单

## 立即停用

以下路径在迁移阶段 A 启动后必须立即停用：

- 裸 prompt 直接成章：不经过状态机、不经过门禁、不产生 artifact 的直接 LLM 生成
- 绕过 truth 源的章节生产：不基于 truth snapshot 的 compose

## 过渡兼容

迁移期间保留，但在最终退役前必须确认可移除：

- 旧 skills 的独立 prompt 模板
- 旧 skills 的本地 KB 加载逻辑（wordsmith/references/ 已接管）
- 旧目录中的 research 参考文件（experiment/ 目录保持只读）

## 保留为路由

以下入口点保留，但内部实现改为路由到引擎：

- `snowflake-fiction` 主流程入口 → 路由到 IntentCompilerService
- `chapter-write` skill → 路由到引擎 plan+compose+write
- `quality-check` skill → 路由到引擎 audit
- `novel-export` skill → 路由到引擎 export
- `character-check` skill → 路由到引擎 truth reconcile

## 退役顺序

1. 退役"裸 prompt 直接到章节"路径（迁移阶段 A 时）
2. 退役旧 skill 直接调用生成（迁移阶段 B 逐个完成后）
3. 清理旧目录/脚手架/数据转换逻辑（迁移阶段 C 时）

## 完成标准

迁移完成的判定条件：

- [ ] 没有代码路径从 IntentCompilerService 外部生成章节正文内容
- [ ] 所有章节生产都经过状态机和门禁
- [ ] 所有 LLM 调用都通过 LLMProvider 抽象层
- [ ] 旧 skills 的生成逻辑代码已从代码库中移除
- [ ] plugin.json 中无已废弃的 skill/agent 引用
- [ ] 全部测试绿色（无 skip、无 xfail）
