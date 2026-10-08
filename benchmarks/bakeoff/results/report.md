# Bake-off — automated comparison

_118 runs · sample CV + real JD · temperature 0 for structured, 0.3 for writing_


## ats_content_optimization

| provider | model | mode | ok | wall s | ttft | tok/s | in→out tok | JSON | quality (auto) |
|---|---|---|---|---|---|---|---|---|---|
| gemini | gemini-3.1-flash-lite | none | ✅ | 1.9 | 0.79 | 201.0 | 723→388 | True | no-halluc |
| gemini | gemini-3.5-flash-lite | none | ✅ | 1.7 | 0.77 | 215.1 | 723→370 | True | no-halluc add_skills_owned(['Terraform']) |
| gemini | gemini-3.6-flash | none | ✅ | 18.1 | 16.41 | 20.5 | 723→371 | True | no-halluc add_skills_owned(['FastAPI', 'AWS', 'Terraform']) |
| groq | openai/gpt-oss-120b | none | ✅ | 4.2 | 3.39 | 443.0 | 751→1874 | True | no-halluc add_skills_owned(['Docker']) |
| groq | openai/gpt-oss-20b | none | ❌ | 3.8 | - | 781.2 | 751→3000 | False | FAIL |
| groq | qwen/qwen3.8-27b | none | ✅ | 1.2 | 0.35 | 323.5 | 727→385 | True | no-halluc add_skills_owned(['Docker']) |

## ats_keyword_extraction

| provider | model | mode | ok | wall s | ttft | tok/s | in→out tok | JSON | quality (auto) |
|---|---|---|---|---|---|---|---|---|---|
| gemini | gemini-3.1-flash-lite | json_schema | ✅ | 1.3 | 1.03 | 116.0 | 224→152 | True | kw-cov=1.0 |
| gemini | gemini-3.1-flash-lite | none | ✅ | 1.1 | 0.75 | 134.6 | 224→144 | True | kw-cov=1.0 |
| gemini | gemini-3.5-flash-lite | json_schema | ✅ | 1.2 | 0.82 | 108.4 | 224→129 | True | kw-cov=1.0 |
| gemini | gemini-3.5-flash-lite | none | ✅ | 1.1 | 0.81 | 142.1 | 224→162 | True | kw-cov=1.0 |
| gemini | gemini-3.6-flash | none | ✅ | 7.1 | 6.46 | 20.4 | 224→144 | False | ok |
| groq | openai/gpt-oss-120b | json_schema | ✅ | 1.7 | 1.7 | 288.8 | 491→491 | True | kw-cov=1.0 |
| groq | openai/gpt-oss-120b | none | ✅ | 1.4 | 1.09 | 314.1 | 283→446 | True | kw-cov=1.0 |
| groq | openai/gpt-oss-20b | json_schema | ❌ | 1.8 | - | - | None→None | None | FAIL |
| groq | openai/gpt-oss-20b | none | ✅ | 1.6 | 1.46 | 528.5 | 283→835 | True | kw-cov=1.0 |
| groq | qwen/qwen3.8-27b | json_schema | ✅ | 0.7 | 0.74 | 209.5 | 238→155 | True | kw-cov=1.0 |
| groq | qwen/qwen3.8-27b | none | ✅ | 1.0 | 0.44 | 227.6 | 238→223 | True | kw-cov=1.0 |
| mistral | mistral-small-latest | json_schema | ❌ | 0.2 | - | - | None→None | None | FAIL |
| mistral | mistral-small-latest | none | ❌ | 0.3 | - | - | None→None | None | FAIL |
| nvidia | nvidia/nemotron-3-super-120b-a12b | json_object | ✅ | 11.2 | 11.2 | 107.1 | 233→1200 | False | ok |
| nvidia | nvidia/nemotron-3-super-120b-a12b | none | ✅ | 36.1 | 36.06 | 33.3 | 233→1200 | False | ok |

## conversation_agent

| provider | model | mode | ok | wall s | ttft | tok/s | in→out tok | JSON | quality (auto) |
|---|---|---|---|---|---|---|---|---|---|
| gemini | gemini-3.1-flash-lite | none | ✅ | 2.3 | 0.77 | 187.8 | 231→432 | None | no-halluc |
| gemini | gemini-3.5-flash-lite | none | ✅ | 1.5 | 0.63 | 207.8 | 231→320 | None | no-halluc |
| gemini | gemini-3.6-flash | none | ✅ | 8.1 | 6.67 | 32.4 | 231→261 | None | no-halluc |
| groq | openai/gpt-oss-120b | none | ✅ | 0.9 | 0.39 | 380.4 | 291→350 | None | no-halluc |
| groq | openai/gpt-oss-20b | none | ✅ | 0.7 | 0.66 | 248.6 | 291→179 | None | no-halluc |
| groq | qwen/qwen3.8-27b | none | ✅ | 1.1 | 0.27 | 339.0 | 242→356 | None | no-halluc |
| nvidia | nvidia/nemotron-3-super-120b-a12b | none | ❌ | 0.5 | - | - | None→None | None | FAIL |

## cover_letter

| provider | model | mode | ok | wall s | ttft | tok/s | in→out tok | JSON | quality (auto) |
|---|---|---|---|---|---|---|---|---|---|
| gemini | gemini-3.1-flash-lite | none | ✅ | 1.8 | 0.66 | 205.1 | 1157→365 | None | no-halluc 7-paras! |
| gemini | gemini-3.5-flash-lite | none | ✅ | 1.9 | 0.85 | 171.4 | 1157→317 | None | no-halluc 7-paras! |
| gemini | gemini-3.6-flash | none | ✅ | 12.4 | 11.07 | 24.2 | 1157→299 | None | no-halluc |
| groq | openai/gpt-oss-120b | none | ✅ | 1.8 | 1.05 | 341.0 | 1183→607 | None | no-halluc |
| groq | openai/gpt-oss-20b | none | ✅ | 1.3 | 1.17 | 544.8 | 1183→730 | None | HALLUC(2 millions) |
| groq | qwen/qwen3.8-27b | none | ✅ | 1.3 | 0.54 | 264.9 | 1189→347 | None | HALLUC(2 millions) 8-paras! |

## extract_education

| provider | model | mode | ok | wall s | ttft | tok/s | in→out tok | JSON | quality (auto) |
|---|---|---|---|---|---|---|---|---|---|
| gemini | gemini-3.1-flash-lite | json_schema | ✅ | 1.1 | 0.74 | 100.0 | 1314→105 | True | no-halluc count-ok |
| gemini | gemini-3.1-flash-lite | none | ✅ | 1.2 | 0.87 | 110.3 | 1314→129 | True | no-halluc count-ok |
| gemini | gemini-3.5-flash-lite | json_schema | ✅ | 1.1 | 0.86 | 111.7 | 1314→124 | True | no-halluc count-ok |
| gemini | gemini-3.5-flash-lite | none | ✅ | 1.0 | 0.6 | 133.0 | 1314→129 | True | no-halluc count-ok |
| gemini | gemini-3.6-flash | none | ✅ | 7.7 | 7.08 | 16.8 | 1314→129 | True | no-halluc count-ok |
| groq | openai/gpt-oss-120b | json_schema | ✅ | 1.6 | 1.56 | 403.8 | 1459→630 | True | no-halluc count-ok |
| groq | openai/gpt-oss-120b | none | ✅ | 2.0 | 1.68 | 390.4 | 1270→773 | True | no-halluc count-ok |
| groq | openai/gpt-oss-20b | json_schema | ✅ | 1.4 | 1.38 | 402.2 | 1459→555 | True | no-halluc count-ok |
| groq | openai/gpt-oss-20b | none | ✅ | 1.1 | 0.99 | 413.0 | 1270→475 | True | no-halluc count-ok |
| groq | qwen/qwen3.8-27b | json_schema | ✅ | 1.4 | 1.43 | 89.6 | 1314→129 | True | no-halluc count-ok |
| groq | qwen/qwen3.8-27b | none | ✅ | 0.8 | 0.42 | 220.0 | 1314→176 | True | no-halluc count-ok |
| mistral | mistral-small-latest | json_schema | ❌ | 0.2 | - | - | None→None | None | FAIL |
| mistral | mistral-small-latest | none | ❌ | 0.3 | - | - | None→None | None | FAIL |
| nvidia | nvidia/nemotron-3-super-120b-a12b | json_object | ✅ | 32.4 | 32.36 | 48.7 | 1276→1576 | True | no-halluc count-ok |
| nvidia | nvidia/nemotron-3-super-120b-a12b | none | ✅ | 57.3 | 53.29 | 28.6 | 1276→1637 | True | no-halluc count-ok |

## extract_experience

| provider | model | mode | ok | wall s | ttft | tok/s | in→out tok | JSON | quality (auto) |
|---|---|---|---|---|---|---|---|---|---|
| gemini | gemini-3.1-flash-lite | json_schema | ✅ | 1.8 | 0.89 | 201.1 | 1274→352 | True | no-halluc count-ok |
| gemini | gemini-3.1-flash-lite | none | ✅ | 1.8 | 0.84 | 202.3 | 1274→356 | True | no-halluc count-ok |
| gemini | gemini-3.5-flash-lite | json_schema | ✅ | 1.7 | 0.82 | 234.7 | 1274→399 | True | no-halluc count-ok |
| gemini | gemini-3.5-flash-lite | none | ✅ | 2.2 | 1.34 | 171.9 | 1274→380 | True | no-halluc count-ok |
| gemini | gemini-3.6-flash | none | ✅ | 9.4 | 7.73 | 40.4 | 1274→380 | True | no-halluc count-ok |
| groq | openai/gpt-oss-120b | json_schema | ✅ | 3.2 | 3.17 | 388.3 | 1457→1231 | True | no-halluc count-ok |
| groq | openai/gpt-oss-120b | none | ✅ | 4.0 | 3.08 | 357.8 | 1261→1417 | True | no-halluc count-ok |
| groq | openai/gpt-oss-20b | json_schema | ✅ | 2.2 | 2.21 | 667.9 | 1457→1476 | True | no-halluc count-ok |
| groq | openai/gpt-oss-20b | none | ✅ | 1.5 | 1.09 | 720.8 | 1261→1110 | True | no-halluc count-ok |
| groq | qwen/qwen3.8-27b | json_schema | ✅ | 1.4 | 1.37 | 256.9 | 1291→352 | True | no-halluc count-ok |
| groq | qwen/qwen3.8-27b | none | ✅ | 1.3 | 0.32 | 370.0 | 1291→481 | True | no-halluc count-ok |
| mistral | mistral-small-latest | json_schema | ❌ | 0.3 | - | - | None→None | None | FAIL |
| mistral | mistral-small-latest | none | ❌ | 0.2 | - | - | None→None | None | FAIL |
| nvidia | nvidia/nemotron-3-super-120b-a12b | json_object | ✅ | 25.0 | 25.01 | 71.3 | 1269→1785 | True | no-halluc count-ok |
| nvidia | nvidia/nemotron-3-super-120b-a12b | none | ✅ | 40.9 | 31.43 | 47.6 | 1269→1945 | True | no-halluc count-ok |

## extract_projects

| provider | model | mode | ok | wall s | ttft | tok/s | in→out tok | JSON | quality (auto) |
|---|---|---|---|---|---|---|---|---|---|
| gemini | gemini-3.1-flash-lite | json_schema | ✅ | 0.8 | 0.72 | 91.7 | 706→77 | True | no-halluc count-ok |
| gemini | gemini-3.1-flash-lite | none | ✅ | 0.9 | 0.7 | 89.5 | 706→77 | True | no-halluc count-ok |
| gemini | gemini-3.5-flash-lite | json_schema | ✅ | 0.9 | 0.89 | 58.2 | 706→53 | True | no-halluc count-ok |
| gemini | gemini-3.5-flash-lite | none | ✅ | 0.8 | 0.68 | 95.1 | 706→77 | True | no-halluc count-ok |
| gemini | gemini-3.6-flash | none | ✅ | 7.4 | 7.15 | 7.2 | 706→53 | True | no-halluc count-ok |
| groq | openai/gpt-oss-120b | json_schema | ✅ | 1.1 | 0.97 | 300.0 | 729→345 | True | no-halluc count-ok |
| groq | openai/gpt-oss-120b | none | ✅ | 1.0 | 0.82 | 345.0 | 729→345 | True | no-halluc count-ok |
| groq | openai/gpt-oss-20b | json_schema | ✅ | 0.5 | 0.48 | 516.7 | 729→279 | True | no-halluc count-ok |
| groq | openai/gpt-oss-20b | none | ✅ | 0.6 | 0.53 | 465.0 | 729→279 | True | no-halluc count-ok |
| groq | qwen/qwen3.8-27b | json_schema | ✅ | 0.5 | 0.3 | 202.0 | 718→103 | True | no-halluc count-ok |
| groq | qwen/qwen3.8-27b | none | ✅ | 0.5 | 0.3 | 202.0 | 718→103 | True | no-halluc count-ok |
| mistral | mistral-small-latest | json_schema | ❌ | 0.3 | - | - | None→None | None | FAIL |
| mistral | mistral-small-latest | none | ❌ | 0.2 | - | - | None→None | None | FAIL |

## extract_skills

| provider | model | mode | ok | wall s | ttft | tok/s | in→out tok | JSON | quality (auto) |
|---|---|---|---|---|---|---|---|---|---|
| gemini | gemini-3.1-flash-lite | json_schema | ✅ | 0.9 | 0.84 | 72.8 | 868→67 | True | no-halluc count-ok |
| gemini | gemini-3.1-flash-lite | none | ✅ | 1.1 | 0.91 | 30.8 | 868→33 | True | no-halluc count-ok |
| gemini | gemini-3.5-flash-lite | json_schema | ✅ | 0.9 | 0.89 | 43.2 | 868→41 | True | no-halluc count-ok |
| gemini | gemini-3.5-flash-lite | none | ✅ | 0.8 | 0.64 | 54.7 | 868→41 | True | no-halluc count-ok |
| gemini | gemini-3.6-flash | none | ✅ | 5.3 | 5.06 | 8.1 | 868→43 | True | no-halluc count-ok |
| groq | openai/gpt-oss-120b | json_schema | ✅ | 0.8 | 0.84 | 250.0 | 991→210 | True | no-halluc count-ok |
| groq | openai/gpt-oss-120b | none | ✅ | 0.8 | 0.67 | 270.0 | 896→216 | True | no-halluc count-ok |
| groq | openai/gpt-oss-20b | json_schema | ✅ | 0.9 | 0.87 | 371.3 | 991→323 | True | no-halluc count-ok |
| groq | openai/gpt-oss-20b | none | ✅ | 0.6 | 0.59 | 354.0 | 896→223 | True | no-halluc count-ok |
| groq | qwen/qwen3.8-27b | json_schema | ✅ | 0.5 | 0.49 | 120.4 | 877→59 | True | no-halluc count-ok |
| groq | qwen/qwen3.8-27b | none | ✅ | 0.7 | 0.32 | 142.6 | 877→97 | True | no-halluc count-ok |
| mistral | mistral-small-latest | json_schema | ❌ | 0.2 | - | - | None→None | None | FAIL |
| mistral | mistral-small-latest | none | ❌ | 0.3 | - | - | None→None | None | FAIL |
| nvidia | nvidia/nemotron-3-super-120b-a12b | json_object | ✅ | 3.2 | 3.18 | 101.6 | 863→325 | True | no-halluc count-ok |
| nvidia | nvidia/nemotron-3-super-120b-a12b | none | ✅ | 3.6 | 3.23 | 120.7 | 863→437 | True | no-halluc count-ok |

## job_preference_extraction

| provider | model | mode | ok | wall s | ttft | tok/s | in→out tok | JSON | quality (auto) |
|---|---|---|---|---|---|---|---|---|---|
| gemini | gemini-3.1-flash-lite | json_schema | ✅ | 1.2 | 0.71 | 84.0 | 611→105 | True | ok |
| gemini | gemini-3.1-flash-lite | none | ✅ | 0.9 | 0.76 | 122.1 | 611→105 | None | ok |
| gemini | gemini-3.5-flash-lite | json_schema | ✅ | 1.0 | 0.75 | 123.5 | 611→121 | True | ok |
| gemini | gemini-3.5-flash-lite | none | ✅ | 0.9 | 0.77 | 120.2 | 611→113 | None | ok |
| gemini | gemini-3.6-flash | none | ✅ | 20.2 | 20.0 | 2.4 | 611→48 | None | ok |
| groq | openai/gpt-oss-120b | json_schema | ✅ | 1.8 | 1.78 | 354.4 | 634→638 | True | ok |
| groq | openai/gpt-oss-120b | none | ✅ | 2.0 | 1.91 | 350.7 | 634→705 | None | ok |
| groq | openai/gpt-oss-20b | json_schema | ❌ | 1.9 | - | 634.9 | 634→1200 | False | FAIL |
| groq | openai/gpt-oss-20b | none | ✅ | 1.6 | 1.5 | 559.0 | 634→872 | None | ok |
| groq | qwen/qwen3.8-27b | json_schema | ✅ | 0.9 | 0.33 | 308.1 | 603→265 | True | ok |
| groq | qwen/qwen3.8-27b | none | ✅ | 0.8 | 0.31 | 311.8 | 603→265 | None | ok |

## profile_analysis

| provider | model | mode | ok | wall s | ttft | tok/s | in→out tok | JSON | quality (auto) |
|---|---|---|---|---|---|---|---|---|---|
| gemini | gemini-3.1-flash-lite | json_schema | ✅ | 1.7 | 0.84 | 164.5 | 811→273 | True | no-halluc |
| gemini | gemini-3.1-flash-lite | none | ✅ | 1.6 | 0.76 | 144.2 | 811→225 | True | HALLUC(70%,2 million) |
| gemini | gemini-3.5-flash-lite | json_schema | ✅ | 1.4 | 0.8 | 166.9 | 811→232 | True | no-halluc |
| gemini | gemini-3.5-flash-lite | none | ✅ | 1.4 | 0.66 | 170.8 | 811→234 | True | HALLUC(73%) |
| gemini | gemini-3.6-flash | none | ✅ | 9.0 | 8.97 | 6.6 | 811→60 | False | ok |
| groq | openai/gpt-oss-120b | json_schema | ✅ | 2.1 | 2.12 | 429.7 | 1024→911 | True | HALLUC(70%) |
| groq | openai/gpt-oss-120b | none | ✅ | 2.6 | 2.21 | 337.1 | 834→890 | True | HALLUC(70%) |
| groq | openai/gpt-oss-20b | json_schema | ✅ | 2.0 | 1.99 | 617.1 | 1024→1228 | True | no-halluc |
| groq | openai/gpt-oss-20b | none | ✅ | 1.5 | 1.29 | 566.2 | 834→838 | True | no-halluc |
| groq | qwen/qwen3.8-27b | json_schema | ✅ | 0.8 | 0.84 | 267.9 | 823→225 | True | HALLUC(73%) |
| groq | qwen/qwen3.8-27b | none | ✅ | 0.8 | 0.31 | 304.8 | 823→253 | True | HALLUC(73%) |
| mistral | mistral-small-latest | json_schema | ❌ | 0.3 | - | - | None→None | None | FAIL |
| mistral | mistral-small-latest | none | ❌ | 0.3 | - | - | None→None | None | FAIL |
| nvidia | nvidia/nemotron-3-super-120b-a12b | json_object | ❌ | 0.6 | - | - | None→None | None | FAIL |
| nvidia | nvidia/nemotron-3-super-120b-a12b | none | ✅ | 11.9 | 11.93 | 125.7 | 839→1500 | False | ok |