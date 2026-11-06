<!-- trustbench-report -->
## TrustBench: ⚠️ inconclusive

Run `cand0001` · `python:app:fn@v1` · dataset `support` · 60 scored cases · policy `ci`

### Release gate

| Rule | Result | Required | Observed |
|---|---|---|---|
| evaluation_complete | ✅ pass | the run finished and every result is scored | complete |
| minimum_cases | ✅ pass | at least 10 scored cases | 60 scored cases |
| overall.minimum_score | ⚠️ inconclusive | at least 80.0% (low end of the 95% interval) | 80.0% (95% interval 68.2% to 88.2%), n=60 |
| overall.maximum_regression | ⚠️ inconclusive | fall by at most 5.0% from the baseline | change -8.3% (95% interval -18.8% to +1.5%), n=60 |

### Pass rate

| Group | Pass rate (95% interval) | Baseline | Change | Verdict |
|---|---|---|---|---|
| overall | 80.0% (68.2% to 88.2%), n=60 | 88.3% | -8.3 pts | cannot tell |
| billing | 85.0% (64.0% to 94.8%), n=20 | 80.0% | +5.0 pts | cannot tell |
| refund | 95.0% (76.4% to 99.1%), n=20 | 90.0% | +5.0 pts | cannot tell |
| shipping | 60.0% (38.7% to 78.1%), n=20 | 95.0% | -35.0 pts | worse |

Compared with run `base0001` on 60 shared cases (mcnemar; a change counts past 1.0%).

### Slices that got worse

| Slice | Cases | Baseline | Now |
|---|---|---|---|
| task = shipping | 20 | 95.0% | 60.0% |

### Cost and speed

| Measure | Value |
|---|---|
| Latency P50 / P95 | 1.23 s / 1.26 s |
| Call failure rate | 0.0% |
| Cost per success | $0.0013 |

### How 12 case(s) failed

| Pattern | Cases | Meaning |
|---|---|---|
| wrong_answer | 12 | The answer does not match the reference |

| Case | Labels | Why |
|---|---|---|
| case-019 | wrong_answer | Expected one of &#91;'Paris'&#93;. |
| case-032 | wrong_answer | Expected one of &#91;'Paris'&#93;. |
| case-033 | wrong_answer | Expected one of &#91;'Paris'&#93;. |
| case-034 | wrong_answer | Expected one of &#91;'Paris'&#93;. |

…and 8 more. Run `trustbench failures cand0001` for all of them.

<sub>TrustBench 0.1.0. Intervals are 95%. A small number of cases gives a wide interval.</sub>
