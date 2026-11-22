# 青岚河流域观测日仓

青岚河流域观测资料中心用日仓处理石桥站的离线报送。总仓限制当天对下游供数的行数。北岸分仓把现场原表标准化后发布，退回闲置额度。次班按原回执复领行账，核对两地实际供数、缺测和在途额度。

## 运行

使用 Python 3.11 标准库。在项目根目录运行平台提供的完整回归入口和独立演示。

```text
python3 -B check_project.py
python3 -B demo.py
```

也可以直接发现同一套 `tests` 回归。

```text
python3 -B -m unittest discover -s tests -t .
```

demo 使用临时目录内的两个 SQLite 库。它沿九步工作流程执行开账、签发、接纳、暂存、发布、退额、核销及复用、重开领取和联合核账。分仓源行 12、16、20 最终为 7 升、缺测、11 升。总仓复用退额后发布 7 行，第八行拒绝。联合日账为 10 行、2 行缺测、41 升。`POINTWISE_SCENARIO` 行包含原始输入、独立预期和断言通过后的实际业务结果。

## Python 接口

| 业务 | 模块和调用 | 结果 |
| --- | --- | --- |
| 开库 | `day.workspaces.open_hub_store(path, hub_id, branch_id, key)` 与 `open_branch_store(path, branch_id, hub_id, key)` | 支持 `with` 的 `WarehouseStore` |
| 开账与查额 | `day.calendar.parse_station_day(station, iso_day)`，`day.budgets.open_day(hub, day, limit)` 与 `get_budget(hub, day)` | 固定起始账及当前日账分别可读 |
| 划拨 | `day.allocations.grant(hub, day, grant_id, branch_id, count)`，`exchange.grants.accept_grant(branch, document)` | 正式凭据及固定接纳结果 |
| 暂存 | `intake.batches.stage(store, day, batch_id, source, entries)` | 有序标准行批 |
| 准备与发布 | `publish.prepare.prepare_batch(store, day, batch_id, release_id, grant_id=None)`，`publish.commit.commit_prepared(store, prepared)` 或 `publish_batch(store, day, batch_id, release_id, grant_id=None)` | 正式成果和原回执组成的二元组 |
| 分批退额 | `exchange.returns.return_quota(branch, day, grant_id, request_id, count)` | 正式退额凭据及当时分仓余量 |
| 核销 | `exchange.settlements.settle_return(hub, document)` | 本件新增恢复量、累计覆盖范围及当时总仓余量 |
| 领取与核账 | `audit.lookup.read_batch(store, release_id)`，`audit.snapshots.read_day_snapshot(store, day)`，`audit.reconcile.audit_day(day, hub_snapshot, branch_snapshot)` | 固定成果读视图、本地快照及两仓日账 |

`key` 是两仓预配置的同一段至少 16 字节的可信交接密钥。V1 的每个仓库文件属于一个固定角色、仓名和对端。重开沿用原配置，两仓分别提交。凭据经过带类型区分的签记校验，绑定指定仓库和原划拨。可用 `exchange.documents` 的 `dump_grant`、`load_grant`、`dump_return`、`load_return` 往返传输文本，接纳及核销仍须执行相应业务接口。配置相同密钥的两仓是可信发行方。

站日使用站名和 `YYYY-MM-DD`；额度、源行号与读值以 Python 整数处理，布尔值拒绝。初始额度可为零，划拨和退额须为正。`entries` 是有限的有序行序列，每项使用 `(源行号, 读值, 单位)`，源行号须为正，单位为 `L` 或 `ml`。整数毫升须整除 1000。`None` 表示明确缺测且仍校验单位。每个输入位置独立计量，缺测占一行且不计升数。整数持久化采用无损文本。暂存校验任何一行失败时无部分批次。空报送可产生零行成果。

划拨号在总仓唯一，退额请求号在分仓唯一，报送号和成果号在各自仓库唯一。每份报送可正式发布一次。开账同参复领初始账，其他已成功请求同参复领原结果，异参以 `ValueError` 拒绝。准备项保留行批和额度身份，提交时才裁决可用额。同一划拨的分仓发布和正式退额都扣减当时可用余额。额度不足抛出 `day.balance.QuotaExceeded`。发布、退额及核销接受可选 `before_commit` 回调，回调抛出的异常使本次事务回滚。回执中的余量固定为当时提交结果。总仓当前余额用 `get_budget` 读取，两仓当前资格也出现在日账快照中。

退额凭据携带本次行数和累计正式退出行数。后件可先按较大的累计范围恢复资格，随后到达且已覆盖的前件新增为零。重投返回原受理结果。核对使用完整仓库、站日和划拨归属，统计正式成果，分别列出尚未接纳的已发划拨和已退未恢复的行额度。`quota.difference` 为初始额度减去实发、两仓可用及在途额度。两份日账宜在同一业务停顿点采集。错站日或错仓组合拒绝，异时点交接差异可从 `quota.issues` 和差额查看。
