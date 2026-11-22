# 农险果园雹灾定损采证台

保单地块版本、冰雹影响带、落果与树冠抽样、灾后采收记录需要共同决定赔付，查勘与补录离线进行。

三套子系统通过原始证据、SQLite 状态与版本化决定互相影响。

```powershell
python -m pip install . --no-deps --no-build-isolation
python -m hailclaim init --db work.db
python -m hailclaim demo --db work.db
python -m unittest discover -s tests -v
```

命令：policy, survey, claim。每个命令通过 `--fixture` 读取单一子系统的 JSON；`demo` 依次执行正常样例。每项报告列证据 ID 和决定版本。

