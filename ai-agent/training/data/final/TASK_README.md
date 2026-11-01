# Hana task dataset (stage 2)

pskit version 2.10.0. Built by `tools/build_dataset.py` (seed 2200, 5% validation per category) from a_qa.jsonl, b_build.jsonl, c_fix.jsonl, d_debug.jsonl, e_safety.jsonl, f_explain.jsonl, g_hana.jsonl.

Providers: aws, azure, gcp, onprem only; left out 169 older records (alibaba 27, cloudflare 26, digitalocean 25, huawei 25, ibm 22, oracle 17, tencent 27).

- train: 257
- val: 14

## Per category

| category | count |
|---|---|
| a_qa | 72 |
| b_build | 64 |
| c_fix | 47 |
| d_debug | 36 |
| e_safety | 25 |
| f_explain | 14 |
| g_hana | 13 |

## Per provider

| provider | count |
|---|---|
| aws | 87 |
| azure | 47 |
| gcp | 59 |
| onprem | 78 |

## Per persona

| persona | count |
|---|---|
| aws_migrator | 32 |
| beginner | 61 |
| detailed_spec | 52 |
| informal_non_native | 47 |
| pasted_error | 28 |
| terse_expert | 51 |

## Per mode

| mode | count |
|---|---|
| ask | 201 |
| auto | 70 |

## Per validate result

| result | count |
|---|---|
| fail | 2 |
| n/a | 101 |
| pass | 168 |

## Per invoke result

| result | count |
|---|---|
| fail | 2 |
| n/a | 162 |
| pass | 107 |
