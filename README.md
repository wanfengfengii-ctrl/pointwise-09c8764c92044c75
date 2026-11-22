# 河湾水文观测日仓

河湾水文资料中心用独立的源仓和区仓处理站点日报。白班核对正式日仓，档案员领取本区材料并入账，夜班按刊核读原分录和回执。库管在刊发材料齐备后清理到期导入明细。

## 运行

需要 Python 3.11 和标准库 SQLite。在仓库根目录运行 `python3 -B check_project.py` 执行完整原回归，运行 `python3 -B demo.py` 演示收件、换归更正、冻结投递、清源和离线核账。演示在测试临时目录中创建独立数据库和区材料文件，业务输入来自 `sample-day.json`。

## 业务日操作

1. 用 `open_source(path)` 打开源仓。白班把 `OpeningBatch(day, request_id, districts, observations, memberships)` 传给 `open_day(db, batch)`。区范围在开日后固定。所有批次行和区号使用元组。
2. 观测用 `ObservationInput(observation, station, litres, position)` 表示，归属用 `MembershipInput(station, district, position)` 表示。`SourcePosition(document, row)` 指原件和从 1 起算的行号。同一请求内任何两行不得共用位置。同日同号且原参数相同可复领，行顺序属于原参数。重跑开日不会覆盖后续正式更正。
3. 白班使用 `apply_batch(db, CorrectionBatch(day, request_id, observations, memberships))`。量值更正用 `ValueCorrection(observation, litres, position)`，归属更正沿用 `MembershipInput`。观测参数或归属参数可为空，两者都为空仍可提交一个正式刊。成功结果的 `edition` 是正式刊号，`projections` 含各区当时的在账行与计量。`project_day(db, day, number=None)` 和 `project_district(db, day, district, number=None)` 支持按刊复核，省略刊号读当前正式截面。
4. 通过 `release_edition(db, day, district, number)` 领取已正式提交的 `FrozenEdition`。档案员可以用 `encode_edition(material)` 写出材料并将其原字节交给区仓，`decode_edition` 可离线检查编码。材料只包含本区有变动的业务行，退出用旧出处，进入用新出处。同区更正先退后进，零变化也可领取原刊。区号、日和刊号都属于材料身份。编码为 V1 规范 JSON 字节。
5. 用 `open_district(path, district)` 打开独立区仓。`receive_edition(db, payload)` 接受本区材料。日初刊在空账上起算。接件后缺前刊时返回 `pending`，前序补齐时按连续刊办结。`frontier` 为已办结前沿，`result` 为本次所投刊的办结结果或 `None`，`completed` 给出本次新办结的各刊结果。重投已办结的同件可复领原执，且 `completed` 为空。`read_balance(db, day, district)` 返回当前区账计量。
6. 夜班用 `read_edition(db, day, district, number)` 离线取得该刊原序进退、报送出处、进量、退量、净变化、当时的区账行数与总量及原回执。关闭源仓、后续更正或区仓重开都不影响旧结果。
7. 库管调用 `expire_input(db, day, through)`，将通过 `through` 刊及更早的导入明细视为已到期。只有该日当前全部正式刊次的各区材料都已完整冻存，才清理到期明细。结果报告 `removed` 行数和 `blocked` 的缺件 `(刊号, 区号)`。管理员可用 `warehouse.intake.source_rows.list_expired_rows` 查看指定范围仍在库的原始行。重试已办请求不会重新导入已清理数据。

## 数据和失败语义

业务日为 `YYYY-MM-DD`。单条观测是 0 至 2⁶³−1 L 的精确整数，0 是有效实测值。总量按整数累加，允许超出单条上限。事实、归属、正式刊和请求结果随源事务一起生效。换归覆盖该站全部在账行。区仓只用本区材料及本地账，办结时核验退出对应当前行、进入不会覆盖现有行，再一起提交余额、分录、回执和前沿。材料来自业务交付渠道，规范编码和回执标识用于比对同件。

`ValueError` 表示非法输入、同号异参或材料冲突。`LookupError` 表示所请求的正式日、区、刊或已办结结果不存在。持久记录内部不一致或 SQLite 错误会传给调用方。源提交与实收入账可选用 `before_commit` 无参回调触发提交前中止，回调异常随操作返回且本次事务回滚。回调应只执行观察或中止。调用方应关闭公开打开函数返回的连接，并让每个高层写入口管理自己的事务。源仓及各区仓使用自己的文件。

固定回归覆盖开日复领、批内位置冲突、同批改值换归、历史截面、大整数、无效批重试、未编辑行随站换归、空刊和乱序、重开、区材料冲突、事务中止、冻存覆盖与断源复核。演示中东区从 7 L 变为 0 L，西区从 0 L 变为 11 L，东区退出沿用晨报出处，西区进入指向更正报送行。
