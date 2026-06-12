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

### 1. 解决的问题

v0.3 解决的核心问题是：科研 Agent 的建议必须有本地依据、可追溯、可测试。
v0.2 已经完成了平台骨架，包括 Code Agent、AlgorithmCard、ExperimentSpec、
JobSpec、DryRunBackend 和 Experiment Planner Agent skeleton。但那一版的
planner 还不能稳定回答“为什么推荐这个参数”或“为什么这个失败模式应该用
某个算法处理”。如果只靠 LLM 自己生成回答，面试官或实验室用户会追问三个
问题：这些建议来自哪里，能不能复现，后续改知识时会不会悄悄破坏检索结果。

具体到 FWI 场景，用户常见问题不是“FWI 是什么”这么简单，而是“低频缺失时，
我应该先做多尺度 FWI、改用 AWI，还是尝试频率外推”“Marmousi 上 loss 不降
应该检查频带、步长、初始模型还是梯度实现”。这些问题需要把论文方法、算法
适用前提、失败案例和历史实验经验关联起来。v0.3 的目标就是把这些知识从
散落的 prompt 和 Markdown，升级成结构化、可版本管理、可被 C++ 代码读取
和测试的本地知识库。

这一步也是为了后续 v0.4 Experiment Planner 做准备。Planner 要生成参数表、
风险分析、下一步计划和 dry-run JobSpec，不能凭空写。它需要先查本地知识：
某个方法适用于什么数据集，什么失败模式和它相关，某个参数应该怎么调，建议
成立的前提是什么。v0.3 先把“知识地基”打好，v0.4 才能把这些知识组织成更像
真实科研实验规划的输出。

### 2. 实现方式

v0.3 的实现选择是“统一 note schema + typed directories + deterministic
retrieval”。没有一开始就上数据库或向量库，也没有给 PaperNote、
AlgorithmNote、ExperimentNote、FailureCase 写四套完全独立的 C++ 类。原因是
当前阶段最重要的是稳定字段、校验规则和检索契约，而不是追求复杂基础设施。

统一模型是 `ResearchKnowledgeNote`。它用 `note_type` 区分四类知识：

- `paper`：表示论文或方法性结论，例如 adjoint-state gradient 的检查原则。
- `algorithm`：表示算法使用指南，例如 multi-scale FWI 或 AWI。
- `experiment`：表示实验记录或 dry-run baseline，例如 Marmousi 多尺度 FWI。
- `failure_case`：表示常见症状和诊断，例如低频缺失导致 cycle skipping。

每条 note 都包含面向检索和解释的字段。`id` 是稳定标识，方便测试和引用；
`methods` 表示相关方法；`datasets` 表示适用数据集或数据形态；`assumptions`
记录建议成立的前提；`parameters` 表示涉及哪些参数；`failure_modes` 记录
和哪些失败模式相关；`parameter_advice` 把参数名映射到具体建议。这些字段
让系统可以回答“为什么这个参数建议和这个方法/失败模式/数据集有关”。

本地资源目录采用 typed directories：

```text
resources/research_knowledge/
  papers/
  algorithms/
  experiments/
  failure_cases/
```

`ResearchKnowledgeBase::load_from_directory(...)` 负责从这些目录加载 `*.json`。
它会按固定目录集合扫描 JSON 文件、排序、解析、校验，再替换内存里的 notes。
如果目录不存在、路径不是目录、JSON 解析失败或 note 字段不合法，会通过
`error` 输出清晰错误。这个设计对面试很重要，因为它说明知识库不是“随便读
文件”，而是有明确的数据质量边界。

检索接口保持确定性，当前包括：

- `filter_by_note_type(note_type)`：按知识类型取 paper/algorithm/experiment/failure_case。
- `filter_by_method(method)`：按方法取 multi-scale-fwi、awi、adjoint-state-gradient 等。
- `filter_by_dataset(dataset)`：按数据集或数据形态取 marmousi、field-shot-gather 等。
- `find_by_failure_mode(failure_mode)`：按失败模式取 cycle_skipping、unstable_gradient 等。
- `parameter_advice_for(method, parameter)`：按方法和参数取具体建议。

其中 `find_by_failure_mode(...)` 有一个小设计点：它会把 `failure_case` 类型排在
前面。原因是用户按失败模式检索时，最需要先看到诊断类知识，然后再看相关算法
或论文。比如查 `cycle_skipping`，先返回“低频缺失导致 cycle skipping”的失败
案例，比先返回某个算法介绍更符合产品语义。

### 3. 关键文件和数据流

关键实现文件：

- `research/include/agent_rpc/research/research_knowledge.h`
- `research/src/research_knowledge.cpp`

关键测试文件：

- `tests/test_research_knowledge.cpp`

关键资源文件：

- `resources/research_knowledge/algorithms/multiscale_fwi.json`
- `resources/research_knowledge/algorithms/awi.json`
- `resources/research_knowledge/papers/multiscale_fwi_practice.json`
- `resources/research_knowledge/papers/adjoint_state_gradient.json`
- `resources/research_knowledge/experiments/marmousi_multiscale_fwi_dry_run.json`
- `resources/research_knowledge/failure_cases/cycle_skipping_low_frequency.json`

数据流可以这样理解：

```text
JSON note files
  -> ResearchKnowledgeBase::load_from_directory
  -> ResearchKnowledgeNote::from_json
  -> ResearchKnowledgeNote::validate
  -> in-memory vector<ResearchKnowledgeNote>
  -> deterministic retrieval methods
  -> future Experiment Planner context
```

这个数据流的好处是每一层都可以单独解释和测试。JSON 是可 code review 的知识
输入；`from_json` 是解析层；`validate` 是数据质量门禁；内存 vector 是简单的
本地索引；检索方法是 planner 后续要依赖的稳定 API。后续如果接 embedding 或
向量库，也可以把这些 note 作为高质量输入，而不是直接把散文文档扔进索引。

### 4. 测试和 TDD 思路

v0.3 的测试重点不是测“LLM 回答得好不好”，而是测 deterministic non-LLM
pieces，也就是本地知识模型和检索契约。这样做的原因是：LLM 输出不稳定，
但知识加载、字段校验、检索结果应该稳定。如果这些基础能力没有测试，后续
planner 即使看起来能回答，也可能是在靠 prompt 碰运气。

关键测试覆盖：

- `ResearchKnowledgeNoteTest.ParsesAndValidatesStructuredNote`：验证 JSON 字段能
  正确进模型，`parameter_advice` 这类 map 字段不会丢。
- `LoadsSeedNotesFromTypedDirectories`：验证 typed directories 能被加载，且可以
  按 id、note type、method 找到 seed notes。
- `RetrievesAdviceByFailureModeAndMethod`：验证 `cycle_skipping` 能返回失败案例，
  并能找到 multi-scale FWI 的 `frequency_band` 建议。
- `CoversAwiAndAdjointStateGradientNotes`：验证 AWI 和 adjoint-state gradient
  不是只写进文件，而是真的能被 method/failure/parameter 检索命中。
- `RetrievesNotesByDataset`：验证 roadmap 要求的 dataset retrieval，包括
  `marmousi`、`field-shot-gather` 和未知数据集空结果。
- `RejectsInvalidNotesWithClearError`：验证坏 note 会被拒绝，并返回包含文件名和
  具体错误的消息。

这次也按 TDD 做了两个重要 RED：

- 先写 `ResearchKnowledge` 测试，构建失败在缺少 `research_knowledge.h`，再实现模型。
- 先声明并调用 `filter_by_dataset(...)`，构建失败在 undefined reference，再实现
  dataset 过滤。

面试时可以强调：这不是“写完代码再补测试”，而是先用测试把知识库能力和
检索契约定义出来，再写最小实现让测试通过。这样每个能力都有失败证据和通过
证据，工程可信度更高。

### 5. 安全和产品边界

v0.3 仍然严格不做真实执行。它不会运行 CUDA/MPI，不会连接 SSH、Slurm、PBS，
不会访问远程服务器，也不会从用户输入执行 shell 命令。所有能力都限制在本地
JSON 文件加载和内存检索。

这个边界不是功能缺失，而是有意的产品分层。科研计算平台里，“知识建议”和
“真实执行”是两个风险等级完全不同的能力。知识建议可以先做到可追溯、可测试、
可审计；真实执行则需要认证授权、工作目录隔离、资源配额、日志收集、artifact
管理、失败恢复和审计记录。v0.3 先把“建议为什么合理”做扎实，后面再进入
v0.4 的实验规划和更晚版本的执行后端。

Code Agent 的边界也保持不变：默认只读，可以提出 patch 建议，但不会自动应用。
Research Knowledge Base 同样是只读知识层，不会改变实验文件，也不会提交任务。

### 6. 设计取舍：为什么先 JSON 而不是数据库或向量库

如果面试官问“为什么不用数据库”，可以这样解释：当前阶段的目标是沉淀知识
schema 和检索契约。JSON 文件更适合早期迭代，因为它们可以直接 code review，
可以和代码一起版本管理，也容易在测试里构造临时坏数据来验证错误处理。数据库
适合后续数据规模变大、多人编辑或需要查询优化时再引入。

如果面试官问“为什么不用向量库”，可以这样解释：向量检索解决的是语义召回，
但它不能替代结构化字段和确定性测试。如果没有 `methods`、`failure_modes`、
`datasets`、`parameter_advice` 这些字段，向量库召回了文档也不一定能解释为什么
这个参数建议成立。v0.3 先做结构化知识治理，后续可以把这些 note 再送入
embedding index。这样 RAG 的输入质量更高。

如果面试官问“统一 Note 模型会不会太粗”，可以这样解释：这是阶段性取舍。
早期四类 note 共享大量字段：方法、数据集、假设、参数、失败模式、建议。统一
模型能减少重复代码，让加载和检索先稳定下来。等 v0.4/v0.5 发现某类 note 有
明显独立行为，再拆出更具体的类型也不晚。

### 7. 面试怎么讲

项目介绍版：

> v0.3 我做的是 Research Knowledge Base。它把 FWI 相关的论文结论、算法说明、
> 实验记录和失败案例建模成结构化 JSON note，并用 C++ 的 `ResearchKnowledgeBase`
> 做本地加载、校验和确定性检索。它支持按方法、失败模式、参数建议和数据集
> 检索知识，并用 GoogleTest 锁住这些检索契约。这样后续 Experiment Planner
> 生成参数表和风险分析时，可以引用本地知识来源，而不是完全依赖 LLM 泛化回答。

技术深挖版：

> 我把知识 note 设计成 schema 化数据，核心字段包括 `methods`、`datasets`、
> `assumptions`、`parameters`、`failure_modes` 和 `parameter_advice`。加载时做
> JSON parse 和 validate，坏数据会带文件名和错误原因返回。检索接口是确定性的，
> 例如 `filter_by_method`、`find_by_failure_mode`、`parameter_advice_for` 和
> `filter_by_dataset`。测试里覆盖了 cycle skipping、多尺度 FWI、AWI、adjoint-state
> gradient 和 Marmousi 数据集，保证后续改知识文件不会悄悄破坏 planner 的上下文。

安全边界版：

> 我没有在 v0.3 接真实 CUDA/MPI 或 Slurm，因为知识建议和作业执行是两层能力。
> v0.3 只读本地 JSON，提供可追溯建议；真实执行需要认证、隔离、日志、artifact
> 和审计，应该在后续 backend milestone 里做。这个分层能避免聊天接口直接获得
> 高风险执行能力。

STAR 版：

> Situation：项目已有多 Agent 和 dry-run planner skeleton，但科研建议缺少可追溯
> 知识来源。Task：把 FWI 参数建议、失败模式和实验经验结构化，给后续 planner
> 使用。Action：设计 `ResearchKnowledgeNote` schema，建立 typed JSON 目录，
> 实现 C++ 加载、校验和按 method/failure/dataset/parameter 的检索，并用 TDD
> 覆盖关键知识点。Result：v0.3 形成了可版本管理、可测试的本地知识库，全量
> CTest 21/21 通过，为 v0.4 生成结构化实验计划打下基础。

### 8. 后续怎么接到 v0.4

v0.4 的自然下一步不是继续堆更多知识文件，而是把知识库接进 Experiment Planner
的 deterministic context。具体可以这样做：

- 根据用户问题提取 method、dataset、failure mode 和 parameter hints。
- 用 `AlgorithmRegistry` 找候选算法卡，例如 `fwi-cuda-mpi`。
- 用 `ResearchKnowledgeBase` 找相关 note，例如 AWI、cycle skipping、Marmousi。
- 把算法卡和知识 note 汇总成稳定 prompt context。
- 让 planner 输出算法推荐、参数表、风险分析、ExperimentSpec 和 dry-run JobSpec。

这样 v0.4 的 planner 就不只是“会说实验计划”，而是能把每个建议关联回本地
知识来源和算法元数据。
