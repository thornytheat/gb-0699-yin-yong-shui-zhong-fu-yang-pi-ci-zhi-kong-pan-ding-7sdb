# 深度登记 饮用水重复样与批次质控判定

- 规模：中型
- 核心规则：双样相对偏差分母零时单独判定；低浓度用绝对差阈值，高浓度用相对偏差；加标回收扣除本底

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 样品接收 | 运行样品接收并核对证据 | `POST /api/1` | `app/modules/m1_samples/rules.py` | `tests/test_samples.py` |
| M2 检测项契约 | 运行检测项契约并核对证据 | `POST /api/2` | `app/modules/m2_contract/rules.py` | `tests/test_contract.py` |
| M3 平行样核对 | 运行平行样核对并核对证据 | `POST /api/3` | `app/modules/m3_parallel/rules.py` | `tests/test_parallel.py` |
| M4 加标回收 | 运行加标回收并核对证据 | `POST /api/4` | `app/modules/m4_spikes/rules.py` | `tests/test_spikes.py` |
| M5 空白污染 | 运行空白污染并核对证据 | `POST /api/5` | `app/modules/m5_blanks/rules.py` | `tests/test_blanks.py` |
| M6 批次放行 | 运行批次放行并核对证据 | `POST /api/6` | `app/modules/m6_release/rules.py` | `tests/test_release.py` |
| M7 复测追踪 | 运行复测追踪并核对证据 | `POST /api/7` | `app/modules/m7_retest/rules.py` | `tests/test_retest.py` |
| M8 核查保护放行 | 分段放行并追踪核查保护与复测 | `POST /api/check/batches` | `app/modules/m8_check/rules.py` | `tests/test_m8_check_rules.py` |

## 陷阱登记

| TRAP | 现象 | 朴素实现会怎样 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-02 | BOM | 静默采信错误值 | 剔除首列BOM | `app/modules/m1_samples/rules.py` | `tests/test_samples.py::test_sample_bom` |
| TRAP-06 | 全角千分位 | 静默采信错误值 | NFKC转Decimal | `app/modules/m2_contract/rules.py` | `tests/test_contract.py::test_contract_unit` |
| TRAP-07 | 空值 | 静默采信错误值 | 缺失隔离 | `app/modules/m2_contract/rules.py` | `tests/test_contract.py::test_contract_sentinel` |
| TRAP-10 | 背景扣除误差 | 静默采信错误值 | Decimal精算 | `app/modules/m4_spikes/rules.py` | `tests/test_spikes.py::test_spike_background` |
| TRAP-14 | 同版冲突 | 静默采信错误值 | 隔离重复版本 | `app/modules/m1_samples/rules.py` | `tests/test_samples.py::test_sample_conflict` |
| TRAP-15 | ug与mg | 静默采信错误值 | 统一mg/L | `app/modules/m2_contract/rules.py` | `tests/test_contract.py::test_contract_unit` |
| TRAP-19 | 多质控失败 | 静默采信错误值 | 明确优先级 | `app/modules/m6_release/rules.py` | `tests/test_release.py::test_release_priority` |
| TRAP-26 | 陈旧复测 | 静默采信错误值 | 版本递增 | `app/modules/m7_retest/rules.py` | `tests/test_retest.py::test_retest_stale` |
| TRAP-31 | 校准漂移后越界放行 | 漂移后结果被直接放行 | 核查切段，失保段不放行只复测 | `app/modules/m8_check/rules.py` | `tests/test_m8_check_rules.py::test_protection_split_on_drift` |
| TRAP-32 | 补核查覆盖丢失原记录 | 重判改写或删除原核查 | 台账只追加，重复编号409 | `app/modules/m8_check/store.py` | `tests/test_m8_check_service.py::test_check_ledger_is_append_only_no_data_loss` |
| TRAP-33 | 重判回收旧放行 | 更新中途原放行被撤回 | 放行只追加，逐样绑定保护核查 | `app/modules/m8_check/service.py` | `tests/test_m8_check_service.py::test_add_pass_check_rejudges_only_after_keeps_old_release` |
| TRAP-34 | 重跑重复生成复测 | 同批反复产生复测任务 | 身份键幂等，重跑不新增 | `app/modules/m8_check/store.py` | `tests/test_m8_check_service.py::test_rerun_after_completion_adds_no_retest_tasks` |

## 对账规则

输入记录=采信记录+隔离记录+空行；重复记录归隔离；算法输出另记业务单位，不冒充输入行数。金额/浓度/时长使用Decimal；证据含原始行号。

M8 对账：样品数=受保护放行数+等待证据数；核查台账只追加，放行记录数只增不减；复测任务按 (检测批,样品,检测项) 唯一，状态 OPEN/DONE。

## 已知不覆盖

不支持超过2MB输入，不执行生产自动放行；异常值需人员复核。
