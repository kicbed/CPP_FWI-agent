# v0.3 Test Report

Date: 2026-06-12

Scope:

- `ResearchKnowledgeNote` and `ResearchKnowledgeBase`.
- JSON-backed paper, algorithm, experiment, and failure-case notes.
- Seed notes for multi-scale FWI, AWI, cycle skipping, adjoint-state gradient,
  and Marmousi dry-run planning.
- Deterministic retrieval by note type, method, failure mode, parameter advice,
  and dataset.

Safety boundaries verified by implementation and docs:

- No real CUDA/MPI execution is enabled.
- No SSH, Slurm, PBS, or remote-server backend is connected.
- No arbitrary shell command execution is wired from user input.
- Research knowledge loading reads local JSON files only.
- v0.3 grounds advice; it does not submit jobs or apply code patches.

## Commands

Final verification commands:

```bash
cmake --build build -j2
ctest --test-dir build -R ResearchKnowledge --output-on-failure
ctest --test-dir build --output-on-failure
git diff --check
```

Final result:

- PASS. `cmake --build build -j2` exited 0.
- PASS. `ctest --test-dir build -R ResearchKnowledge --output-on-failure`
  passed 1/1 tests.
- PASS. `ctest --test-dir build --output-on-failure` passed 21/21 tests.
- PASS. `git diff --check` produced no output.

## Coverage Summary

- `ResearchKnowledgeNoteTest`: verifies JSON parsing, validation, and
  parameter-advice fields.
- `ResearchKnowledgeBaseTest.LoadsSeedNotesFromTypedDirectories`: verifies
  loading from typed local directories and lookup by id, note type, and method.
- `ResearchKnowledgeBaseTest.RetrievesAdviceByFailureModeAndMethod`: verifies
  cycle-skipping retrieval and multi-scale FWI frequency-band advice.
- `ResearchKnowledgeBaseTest.CoversAwiAndAdjointStateGradientNotes`: verifies
  AWI and adjoint-state-gradient content coverage.
- `ResearchKnowledgeBaseTest.RetrievesNotesByDataset`: verifies dataset-based
  retrieval for `marmousi`, `field-shot-gather`, and unknown datasets.
- `ResearchKnowledgeBaseTest.RejectsInvalidNotesWithClearError`: verifies
  invalid note rejection with clear error messages.

## Knowledge Summary

### 1. 解决的问题：为什么 v0.3 要先做 Research Knowledge Base

v0.2 已经有 Code Agent、AlgorithmCard、ExperimentSpec、JobSpec 和
DryRunBackend，但 Experiment Planner 还缺一个稳定的知识地基。用户问“低频
缺失时应该用多尺度 FWI、AWI，还是频率外推？”时，系统不能只靠 LLM 的通用
知识回答；它应该能引用本地结构化知识，说明建议来自哪类材料：论文结论、
算法假设、历史实验记录，还是失败案例诊断。

v0.3 解决的是“科研建议可追溯”的问题。它把研究知识从散落的 Markdown 或
prompt 文字，升级成可以被 C++ 加载、验证、检索和测试的 JSON note。这样
后续 v0.4 的 Experiment Planner 可以先查本地知识，再生成参数表、风险分析
和 dry-run JobSpec。

### 2. 实现方式：用统一 Note 模型承载四类科研知识

v0.3 没有为 PaperNote、AlgorithmNote、ExperimentNote、FailureCase 分别写
四套 C++ 类，而是先使用一个统一的 `ResearchKnowledgeNote` 模型，通过
`note_type` 区分类型。这样可以用一套加载、验证和检索逻辑覆盖四类知识，
避免早期过度抽象。

每条 note 包含：

- `id`：稳定标识，例如 `algorithm.awi`。
- `note_type`：`paper`、`algorithm`、`experiment`、`failure_case`。
- `methods`：适用方法，例如 `multi-scale-fwi`、`awi`。
- `datasets`：适用数据集或数据形态，例如 `marmousi`、`field-shot-gather`。
- `assumptions`：使用建议成立的前提。
- `parameters`：涉及参数，例如 `frequency_band`、`gradient_check`。
- `failure_modes`：关联失败模式，例如 `cycle_skipping`。
- `parameter_advice`：参数到建议文本的映射。

`ResearchKnowledgeBase` 从 `resources/research_knowledge` 下的 typed
directories 加载 JSON 文件，并提供确定性检索接口：

- `filter_by_note_type`
- `filter_by_method`
- `filter_by_dataset`
- `find_by_failure_mode`
- `parameter_advice_for`

### 3. 关键文件/测试：哪些文件体现了 v0.3 能力

关键实现文件：

- `research/include/agent_rpc/research/research_knowledge.h`
- `research/src/research_knowledge.cpp`

关键资源目录：

- `resources/research_knowledge/papers`
- `resources/research_knowledge/algorithms`
- `resources/research_knowledge/experiments`
- `resources/research_knowledge/failure_cases`

关键测试：

- `tests/test_research_knowledge.cpp`

测试覆盖了三类风险：

- 数据质量风险：缺少 `id`、`title`、`note_type`、`summary`、`methods` 时要
  给出清晰错误，而不是静默加载坏数据。
- 检索契约风险：按 method、failure mode、dataset 和 parameter advice
  检索必须返回稳定结果。
- 内容覆盖风险：AWI、adjoint-state gradient、cycle skipping、多尺度 FWI
  等核心科研概念必须能被测试保护，防止后续删改资源时悄悄丢失能力。

### 4. 安全或产品边界：为什么 v0.3 仍然不执行作业

v0.3 只做知识层，不做执行层。它读取仓库内本地 JSON 文件，返回结构化 note
或参数建议；不会提交 CUDA/MPI 作业，不会连接 SSH/Slurm/PBS，不会运行用户
输入命令。

这个边界很重要，因为知识建议和真实执行的风险等级不同。建议层可以先做
本地、可测试、可审计的检索；执行层必须等认证、授权、工作目录隔离、日志
收集、artifact 管理和审计能力成熟之后再接入。v0.3 的输出是“为什么建议
这样设置参数”，不是“已经替你跑了实验”。

### 5. 面试怎么讲：把 v0.3 讲成工程能力而不是资料整理

可以这样讲：

> 我在 v0.3 做的是 Research Knowledge Base，把论文、算法、实验记录和失败
> 案例变成结构化 JSON note，并用 C++ 的 `ResearchKnowledgeBase` 做本地
> 加载、校验和检索。它支持按方法、失败模式、参数建议和数据集查找知识，
> 并用 GoogleTest 保护这些检索契约。这样 Experiment Planner 后续生成
> 参数表和风险分析时，可以引用本地知识来源，而不是完全依赖 LLM 的泛化回答。

如果面试官追问“为什么不用数据库或向量库”，可以回答：

> 这个阶段我先选 JSON + deterministic retrieval，因为目标是稳定建模和安全
> 边界，而不是召回效果最大化。JSON note 便于 code review、版本管理和测试；
> 检索接口先稳定下来，后续可以再把这些 note 索引到 embedding/vector store。
> 这样架构上不会被早期工具选择绑死。

如果面试官追问“这和 RAG 有什么关系”，可以回答：

> 这是 RAG 前的数据治理层。很多项目直接把文档丢进向量库，但没有结构化字段，
> 也没有测试保证“某个失败模式一定能检索到某条知识”。我先定义 note schema、
> 类型目录、字段校验和确定性检索，再把它接到 Planner 或 embedding retrieval。
> 这样 RAG 的输入质量更可控。
