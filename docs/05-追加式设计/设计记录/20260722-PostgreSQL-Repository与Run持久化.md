# 追加项目点设计记录：PostgreSQL Repository 与 Run 持久化

## 元信息

- 状态：实施中
- 作者：Codex
- 创建日期：2026-07-22
- 最后更新：2026-07-22
- 关联模块：API、Runtime、Artifact、配置、存储与高可用
- 关联问题：API Run 注册表为内存 `dict`，服务重启丢失历史；HA-01

## 问题与动机

当前 FastAPI 层使用进程内 `dict[str, _RunRecord]` 保存 Run。Artifact 虽然写入 `runs_root`，但 Run 的身份、目标、workspace、provider、状态和失败原因没有正式 Repository。服务重启后 `/api/runs` 与 `/api/runs/{id}` 无法恢复，后续检查点、幂等、租约、鉴权和预算账本也没有可靠数据地基。

本项目已完成可信度内核，下一阶段目标是高可用运行时。本设计先完成 HA-01 的最小纵向切片：将 API Run 生命周期从内存注册表迁移到可替换 Repository，并提供 PostgreSQL 实现、版本化迁移和重启后历史查询。

## 用户场景

1. 操作者触发 Run 后重启 API，仍能查询历史、终态、失败原因和 Artifact；
2. 运维人员通过迁移创建或升级数据库，而不是依赖应用隐式建表；
3. 测试和本地快速开发可以使用 InMemory Repository，不要求每个单元测试启动 PostgreSQL；
4. 后续 HA-02 可以在相同 Repository 边界上增加 Step、ToolCall、检查点、幂等和租约。

## 产品边界判断

该能力不改变 Evidence、Claim、Verifier、Repair 或模型行为，只补齐运行状态基础设施。它服务于项目的高可用目标，并降低重启丢状态与重复执行的风险。

## 当前实现

- `create_app()` 创建内存 registry 与 lock；
- `routes.py` 直接查询 registry；
- 后台 daemon 线程更新共享 `_RunRecord`；
- Artifact 通过 `output_dir` 从文件系统读取；
- `serve` 没有数据库配置或迁移入口。

## 目标与非目标

### 目标

- 建立不依赖 FastAPI 的 `RunRepository` 协议和稳定 `RunRecord`；
- 提供线程安全 InMemory 实现和同步 PostgreSQL/SQLAlchemy 实现；
- 使用 Alembic 管理 `runs` 表迁移；
- API 的创建、状态更新、列表、详情和 Artifact 定位全部经 Repository；
- CLI/Settings 支持 `WORKPILOT_DATABASE_URL`；
- 数据库配置存在时，服务重启后仍能查询历史 Run；
- 保持现有 HTTP 契约和 Runtime 正确性行为。

### 非目标

- 本切片不实现 Step/ToolCall 检查点；
- 不实现幂等键、租约、任务队列或多租户；
- 不把 Artifact 正文存入数据库；
- 不自动恢复重启前处于 running 的 Run；这类记录暂时原样可见，HA-02 再定义 recovering/orphaned 语义；
- 不在应用启动时自动执行数据库迁移；部署必须显式升级 Schema。

## 备选方案

### 方案一：扫描 `runs_root` 重建索引

实现快，但 Artifact 不包含完整、事务一致的 Run 元数据，无法支撑锁、租约、幂等和租户隔离。仅保留为历史导入或灾备工具方向。

### 方案二：psycopg + 手写 SQL

依赖较少，但需要手工维护映射、事务样板和迁移工具。随着 Step、ToolCall、Budget 和租户表增加，维护成本高。

### 方案三：SQLAlchemy 2.x + psycopg 3 + Alembic

同步模型与当前 Runtime/后台线程一致；Session 可按方法隔离；Alembic 提供显式迁移；后续可使用事务、版本列、`SELECT FOR UPDATE` 和 PostgreSQL 租约能力。

### 保持现状

无法满足服务重启、恢复、并发和企业部署要求，拒绝。

## 最终决策及原因

采用方案三。Repository 保持小接口，SQLAlchemy 类型不泄漏到 API/Runtime。InMemory 实现用于快速测试和显式开发回退，PostgreSQL 实现用于持久化服务。

## 模块与依赖影响

- 新增 `workpilot.persistence`：Record、Protocol、InMemory、SQLAlchemy Model/Repository；
- `api.app`：删除 `_RunRecord` 与 registry，注入 Repository；
- `api.routes`：所有 Run 查询经 Repository；
- `config` / `cli`：增加数据库 URL 和迁移/serve 配置；
- `pyproject.toml`：Web/数据库依赖增加 SQLAlchemy、psycopg、Alembic；
- 新增 Alembic 配置和首个 migration；
- 测试新增 Repository 契约、API 重建和可选 PostgreSQL 集成测试。

## 数据和接口变更

`runs` 最小字段：

- `run_id`：稳定主键；
- `goal`、`workspace`、`provider`；
- `output_dir`：Artifact 目录的绝对路径；
- `state`、`failure_reason`；
- `created_at`、`updated_at`；
- `version`：为 HA-02 乐观并发预留，但本切片不宣称已完成并发控制。

Repository 最小接口：`create`、`get`、`list`、`update_state`、`close`。

HTTP 请求与响应 Schema 不变。列表按 `created_at DESC, run_id DESC` 返回。

## 安全、权限和隐私

- 数据库 URL 属于敏感配置，不进入日志或 API 响应；
- failure_reason 仍可能包含运行异常，本切片沿用现有行为，P1 统一脱敏；
- output_dir 必须由服务端 `runs_root / run_id` 生成，不能接受客户端路径；
- Repository 查询尚无 tenant 条件，因此本切片仍不可公网暴露；HA-03 完成前保持现有安全警告。

## 失败、重试与恢复

- Repository 写入失败时，Run 不得启动后台线程；API 返回 503；
- 创建成功后线程启动失败，Run 更新为 failed；
- 终态写入失败不伪装成功，由日志暴露并保留 Runtime Artifact，后续可人工对账；
- 数据库不可用时不自动切换内存 Repository，避免产生分叉历史；
- 未配置数据库时只有显式开发回退使用 InMemory Repository；
- 重启前 running Run 的恢复语义留给 HA-02。

## 可观测性

本切片不记录 SQL 正文和数据库 URL。服务启动时只记录 Repository 类型。后续增加 Repository 延迟、错误率、连接池和迁移版本指标。

## 实施阶段

1. Repository 协议、Record 与 InMemory 契约测试；
2. SQLAlchemy Model/Repository 与 Alembic migration；
3. API 改为 Repository 驱动，增加重建应用后历史可查测试；
4. Settings、CLI、`.env.example` 和 README；
5. 可选真实 PostgreSQL 集成测试与文档回填。

## 测试与评测

- InMemory Repository CRUD、排序、重复主键、缺失更新；
- SQLAlchemy Repository 契约测试；
- API Run 生命周期保持通过；
- 使用相同 Repository 重建 `create_app` 后历史仍可查询；
- 数据库创建失败时不启动 Runtime；
- 可选 `WORKPILOT_TEST_DATABASE_URL` 真实 PostgreSQL 测试；
- 完整 pytest 与前端构建不应退化。

## 验收标准

- API 不再直接维护 Run `dict`；
- PostgreSQL migration 可从空库升级；
- 配置 PostgreSQL 后，API 重启不丢 Run 历史与终态；
- HTTP 契约与 Artifact 追溯保持兼容；
- 数据库错误 fail closed，不静默回退内存；
- 文档明确本切片不等于检查点恢复、幂等或高可用全部完成。

## 实际实施结果

已完成第一纵向切片：

- 新增不可变 `RunRecord`、`RunRepository`、线程安全 InMemory 实现与稳定错误类型；
- 新增 SQLAlchemy `RunRow` / `SQLAlchemyRunRepository` 和 psycopg/Alembic 依赖组；
- 新增 `20260722_0001` migration，离线生成 PostgreSQL 事务 DDL 通过；
- FastAPI 不再维护 Run `dict`，创建、状态、历史、详情和 Artifact 定位全部通过 Repository；
- CLI 默认要求 `WORKPILOT_DATABASE_URL`，仅显式 `--in-memory-runs` 使用易失开发模式；
- 数据库 URL 使用 SecretStr 且不提供命令行凭证参数；
- 使用隔离 PostgreSQL 17.6 从空库完成 `upgrade`、`downgrade` 和再次 `upgrade`；
- 真实数据库 Repository round-trip，以及关闭旧 Repository、重建 App 后查询历史 Run 与报告均通过；
- Windows/Python 3.12 + PostgreSQL 17.6 完整回归 218 passed，前端生产构建通过；
- 不配置 `WORKPILOT_TEST_DATABASE_URL` 时 2 项真实 PostgreSQL 条件测试会跳过；本切片已验收，HA-01 的 Artifact Metadata、文件一致性和历史导入范围仍待后续完成。

## 遗留问题与后续项目点

- HA-02：Step/ToolCall 检查点、幂等键、租约和 crash recovery；
- HA-03：Tenant/User/Project 与预算隔离；
- P1：任务队列、取消、超时、指标、成本和 Trace 脱敏持久化。
