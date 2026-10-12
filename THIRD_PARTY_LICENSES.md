<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Third-Party Software Licenses

This repository uses the dependencies listed below. Each package link identifies the exact
version resolved in `uv.lock`. Complete collected license and attribution texts are
preserved in [third_party/NOTICES.txt](third_party/NOTICES.txt), grouped by package and
version, with the original archive URLs, checksums, and file paths. SPDX expressions
are inventory metadata only; upstream texts retain their own terms and notices.
Shared standard license texts are also included under `third_party/license_texts/`.

The inventory covers runtime dependencies and all optional extras, including transitive
and platform-specific dependencies. Bundled JSON examples and synthetic test fixtures
are NVIDIA-authored and covered by the root [LICENSE](LICENSE).

Run `make update-licenses` after changing dependencies to collect texts automatically.
`make check-licenses` verifies the generated inventory and collected texts.
See DEVELOPMENT.md for collection scope and exception handling.

| Package | SPDX license expression | License and attribution documents |
| --- | --- | --- |
| [`aiohappyeyeballs 2.7.1`](https://pypi.org/project/aiohappyeyeballs/2.7.1/) | `PSF-2.0` | [NOTICES.txt](third_party/NOTICES.txt), [PSF-2.0.txt](third_party/license_texts/PSF-2.0.txt) |
| [`aiohttp 3.14.3`](https://pypi.org/project/aiohttp/3.14.3/) | `APACHE-2.0 AND MIT` | [NOTICES.txt](third_party/NOTICES.txt), [Apache-2.0.txt](third_party/license_texts/Apache-2.0.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`aiosignal 1.4.0`](https://pypi.org/project/aiosignal/1.4.0/) | `APACHE-2.0` | [NOTICES.txt](third_party/NOTICES.txt), [Apache-2.0.txt](third_party/license_texts/Apache-2.0.txt) |
| [`annotated-doc 0.0.5`](https://pypi.org/project/annotated-doc/0.0.5/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`annotated-types 0.8.0`](https://pypi.org/project/annotated-types/0.8.0/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`anyio 4.15.1`](https://pypi.org/project/anyio/4.15.1/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`attrs 26.1.0`](https://pypi.org/project/attrs/26.1.0/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`boto3 1.43.95`](https://pypi.org/project/boto3/1.43.95/) | `APACHE-2.0` | [NOTICES.txt](third_party/NOTICES.txt), [Apache-2.0.txt](third_party/license_texts/Apache-2.0.txt) |
| [`botocore 1.43.95`](https://pypi.org/project/botocore/1.43.95/) | `APACHE-2.0` | [NOTICES.txt](third_party/NOTICES.txt), [Apache-2.0.txt](third_party/license_texts/Apache-2.0.txt) |
| [`cachetools 7.2.0`](https://pypi.org/project/cachetools/7.2.0/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`certifi 2026.7.22`](https://pypi.org/project/certifi/2026.7.22/) | `MPL-2.0` | [NOTICES.txt](third_party/NOTICES.txt), [MPL-2.0.txt](third_party/license_texts/MPL-2.0.txt) |
| [`cffi 2.1.1`](https://pypi.org/project/cffi/2.1.1/) | `MIT-0` | [NOTICES.txt](third_party/NOTICES.txt), [MIT-0.txt](third_party/license_texts/MIT-0.txt) |
| [`charset-normalizer 3.5.1`](https://pypi.org/project/charset-normalizer/3.5.1/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`click 8.5.0`](https://pypi.org/project/click/8.5.0/) | `BSD-3-CLAUSE` | [NOTICES.txt](third_party/NOTICES.txt), [BSD-3-Clause.txt](third_party/license_texts/BSD-3-Clause.txt) |
| [`cloudpickle 3.1.2`](https://pypi.org/project/cloudpickle/3.1.2/) | `BSD-3-CLAUSE` | [NOTICES.txt](third_party/NOTICES.txt), [BSD-3-Clause.txt](third_party/license_texts/BSD-3-Clause.txt) |
| [`colorama 0.4.6`](https://pypi.org/project/colorama/0.4.6/) | `BSD-3-CLAUSE` | [NOTICES.txt](third_party/NOTICES.txt), [BSD-3-Clause.txt](third_party/license_texts/BSD-3-Clause.txt) |
| [`cryptography 50.0.1`](https://pypi.org/project/cryptography/50.0.1/) | `APACHE-2.0 OR BSD-3-CLAUSE` | [NOTICES.txt](third_party/NOTICES.txt), [Apache-2.0.txt](third_party/license_texts/Apache-2.0.txt), [BSD-3-Clause.txt](third_party/license_texts/BSD-3-Clause.txt) |
| [`databricks-sdk 0.140.0`](https://pypi.org/project/databricks-sdk/0.140.0/) | `APACHE-2.0` | [NOTICES.txt](third_party/NOTICES.txt), [Apache-2.0.txt](third_party/license_texts/Apache-2.0.txt) |
| [`deprecation 2.1.0`](https://pypi.org/project/deprecation/2.1.0/) | `APACHE-2.0` | [NOTICES.txt](third_party/NOTICES.txt), [Apache-2.0.txt](third_party/license_texts/Apache-2.0.txt) |
| [`dirhash 0.5.0`](https://pypi.org/project/dirhash/0.5.0/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`distro 1.9.0`](https://pypi.org/project/distro/1.9.0/) | `APACHE-2.0` | [NOTICES.txt](third_party/NOTICES.txt), [Apache-2.0.txt](third_party/license_texts/Apache-2.0.txt) |
| [`fastapi 0.141.1`](https://pypi.org/project/fastapi/0.141.1/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`fastuuid 0.14.0`](https://pypi.org/project/fastuuid/0.14.0/) | `BSD-3-CLAUSE` | [NOTICES.txt](third_party/NOTICES.txt), [BSD-3-Clause.txt](third_party/license_texts/BSD-3-Clause.txt) |
| [`filelock 3.32.6`](https://pypi.org/project/filelock/3.32.6/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`frozenlist 1.8.0`](https://pypi.org/project/frozenlist/1.8.0/) | `APACHE-2.0` | [NOTICES.txt](third_party/NOTICES.txt), [Apache-2.0.txt](third_party/license_texts/Apache-2.0.txt) |
| [`fsspec 2026.7.0`](https://pypi.org/project/fsspec/2026.7.0/) | `BSD-3-CLAUSE` | [NOTICES.txt](third_party/NOTICES.txt), [BSD-3-Clause.txt](third_party/license_texts/BSD-3-Clause.txt) |
| [`gitdb 4.0.12`](https://pypi.org/project/gitdb/4.0.12/) | `BSD-3-CLAUSE` | [NOTICES.txt](third_party/NOTICES.txt), [BSD-3-Clause.txt](third_party/license_texts/BSD-3-Clause.txt) |
| [`gitpython 3.1.62`](https://pypi.org/project/gitpython/3.1.62/) | `BSD-3-CLAUSE` | [NOTICES.txt](third_party/NOTICES.txt), [BSD-3-Clause.txt](third_party/license_texts/BSD-3-Clause.txt) |
| [`google-auth 2.58.0`](https://pypi.org/project/google-auth/2.58.0/) | `APACHE-2.0` | [NOTICES.txt](third_party/NOTICES.txt), [Apache-2.0.txt](third_party/license_texts/Apache-2.0.txt) |
| [`h11 0.16.0`](https://pypi.org/project/h11/0.16.0/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`h2 4.4.1`](https://pypi.org/project/h2/4.4.1/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`harbor 0.20.0`](https://pypi.org/project/harbor/0.20.0/) | `APACHE-2.0` | [NOTICES.txt](third_party/NOTICES.txt), [Apache-2.0.txt](third_party/license_texts/Apache-2.0.txt) |
| [`hf-xet 1.6.0`](https://pypi.org/project/hf-xet/1.6.0/) | `APACHE-2.0` | [NOTICES.txt](third_party/NOTICES.txt), [Apache-2.0.txt](third_party/license_texts/Apache-2.0.txt) |
| [`hpack 4.2.0`](https://pypi.org/project/hpack/4.2.0/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`httpcore 1.0.9`](https://pypi.org/project/httpcore/1.0.9/) | `BSD-3-CLAUSE` | [NOTICES.txt](third_party/NOTICES.txt), [BSD-3-Clause.txt](third_party/license_texts/BSD-3-Clause.txt) |
| [`httpx 0.28.1`](https://pypi.org/project/httpx/0.28.1/) | `BSD-3-CLAUSE` | [NOTICES.txt](third_party/NOTICES.txt), [BSD-3-Clause.txt](third_party/license_texts/BSD-3-Clause.txt) |
| [`huggingface-hub 1.31.0`](https://pypi.org/project/huggingface-hub/1.31.0/) | `APACHE-2.0` | [NOTICES.txt](third_party/NOTICES.txt), [Apache-2.0.txt](third_party/license_texts/Apache-2.0.txt) |
| [`hyperframe 6.1.0`](https://pypi.org/project/hyperframe/6.1.0/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`idna 3.19`](https://pypi.org/project/idna/3.19/) | `BSD-3-CLAUSE` | [NOTICES.txt](third_party/NOTICES.txt), [BSD-3-Clause.txt](third_party/license_texts/BSD-3-Clause.txt) |
| [`importlib-metadata 8.9.0`](https://pypi.org/project/importlib-metadata/8.9.0/) | `APACHE-2.0` | [NOTICES.txt](third_party/NOTICES.txt), [Apache-2.0.txt](third_party/license_texts/Apache-2.0.txt) |
| [`jinja2 3.1.6`](https://pypi.org/project/jinja2/3.1.6/) | `BSD-3-CLAUSE` | [NOTICES.txt](third_party/NOTICES.txt), [BSD-3-Clause.txt](third_party/license_texts/BSD-3-Clause.txt) |
| [`jiter 0.17.0`](https://pypi.org/project/jiter/0.17.0/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`jmespath 1.1.0`](https://pypi.org/project/jmespath/1.1.0/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`jsonschema 4.26.0`](https://pypi.org/project/jsonschema/4.26.0/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`jsonschema-specifications 2025.9.1`](https://pypi.org/project/jsonschema-specifications/2025.9.1/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`litellm 1.101.0`](https://pypi.org/project/litellm/1.101.0/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`markdown-it-py 4.2.0`](https://pypi.org/project/markdown-it-py/4.2.0/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`markupsafe 3.0.3`](https://pypi.org/project/markupsafe/3.0.3/) | `BSD-3-CLAUSE` | [NOTICES.txt](third_party/NOTICES.txt), [BSD-3-Clause.txt](third_party/license_texts/BSD-3-Clause.txt) |
| [`mdurl 0.1.2`](https://pypi.org/project/mdurl/0.1.2/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`mlflow-skinny 3.16.1`](https://pypi.org/project/mlflow-skinny/3.16.1/) | `APACHE-2.0` | [NOTICES.txt](third_party/NOTICES.txt), [Apache-2.0.txt](third_party/license_texts/Apache-2.0.txt) |
| [`multidict 6.8.0`](https://pypi.org/project/multidict/6.8.0/) | `APACHE-2.0` | [NOTICES.txt](third_party/NOTICES.txt), [Apache-2.0.txt](third_party/license_texts/Apache-2.0.txt) |
| [`openai 2.54.0`](https://pypi.org/project/openai/2.54.0/) | `APACHE-2.0` | [NOTICES.txt](third_party/NOTICES.txt), [Apache-2.0.txt](third_party/license_texts/Apache-2.0.txt) |
| [`opentelemetry-api 1.44.0`](https://pypi.org/project/opentelemetry-api/1.44.0/) | `APACHE-2.0` | [NOTICES.txt](third_party/NOTICES.txt), [Apache-2.0.txt](third_party/license_texts/Apache-2.0.txt) |
| [`opentelemetry-proto 1.44.0`](https://pypi.org/project/opentelemetry-proto/1.44.0/) | `APACHE-2.0` | [NOTICES.txt](third_party/NOTICES.txt), [Apache-2.0.txt](third_party/license_texts/Apache-2.0.txt) |
| [`opentelemetry-sdk 1.44.0`](https://pypi.org/project/opentelemetry-sdk/1.44.0/) | `APACHE-2.0` | [NOTICES.txt](third_party/NOTICES.txt), [Apache-2.0.txt](third_party/license_texts/Apache-2.0.txt) |
| [`opentelemetry-semantic-conventions 0.65b0`](https://pypi.org/project/opentelemetry-semantic-conventions/0.65b0/) | `APACHE-2.0` | [NOTICES.txt](third_party/NOTICES.txt), [Apache-2.0.txt](third_party/license_texts/Apache-2.0.txt) |
| [`packaging 26.3`](https://pypi.org/project/packaging/26.3/) | `APACHE-2.0 OR BSD-2-CLAUSE` | [NOTICES.txt](third_party/NOTICES.txt), [Apache-2.0.txt](third_party/license_texts/Apache-2.0.txt), [BSD-2-Clause.txt](third_party/license_texts/BSD-2-Clause.txt) |
| [`pathspec 1.1.1`](https://pypi.org/project/pathspec/1.1.1/) | `MPL-2.0` | [NOTICES.txt](third_party/NOTICES.txt), [MPL-2.0.txt](third_party/license_texts/MPL-2.0.txt) |
| [`platformdirs 4.11.8`](https://pypi.org/project/platformdirs/4.11.8/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`postgrest 2.31.0`](https://pypi.org/project/postgrest/2.31.0/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`propcache 0.5.3`](https://pypi.org/project/propcache/0.5.3/) | `APACHE-2.0` | [NOTICES.txt](third_party/NOTICES.txt), [Apache-2.0.txt](third_party/license_texts/Apache-2.0.txt) |
| [`protobuf 7.36.0`](https://pypi.org/project/protobuf/7.36.0/) | `BSD-3-CLAUSE` | [NOTICES.txt](third_party/NOTICES.txt), [BSD-3-Clause.txt](third_party/license_texts/BSD-3-Clause.txt) |
| [`pyasn1 0.6.4`](https://pypi.org/project/pyasn1/0.6.4/) | `BSD-2-CLAUSE` | [NOTICES.txt](third_party/NOTICES.txt), [BSD-2-Clause.txt](third_party/license_texts/BSD-2-Clause.txt) |
| [`pyasn1-modules 0.4.2`](https://pypi.org/project/pyasn1-modules/0.4.2/) | `BSD-2-CLAUSE` | [NOTICES.txt](third_party/NOTICES.txt), [BSD-2-Clause.txt](third_party/license_texts/BSD-2-Clause.txt) |
| [`pycparser 3.0`](https://pypi.org/project/pycparser/3.0/) | `BSD-3-CLAUSE` | [NOTICES.txt](third_party/NOTICES.txt), [BSD-3-Clause.txt](third_party/license_texts/BSD-3-Clause.txt) |
| [`pydantic 2.13.5`](https://pypi.org/project/pydantic/2.13.5/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`pydantic-core 2.46.5`](https://pypi.org/project/pydantic-core/2.46.5/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`pydantic-settings 2.15.0`](https://pypi.org/project/pydantic-settings/2.15.0/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`pygments 2.21.0`](https://pypi.org/project/pygments/2.21.0/) | `BSD-2-CLAUSE` | [NOTICES.txt](third_party/NOTICES.txt), [BSD-2-Clause.txt](third_party/license_texts/BSD-2-Clause.txt) |
| [`pyjwt 2.14.0`](https://pypi.org/project/pyjwt/2.14.0/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`python-dateutil 2.9.0.post0`](https://pypi.org/project/python-dateutil/2.9.0.post0/) | `APACHE-2.0 OR BSD-3-CLAUSE` | [NOTICES.txt](third_party/NOTICES.txt), [Apache-2.0.txt](third_party/license_texts/Apache-2.0.txt), [BSD-3-Clause.txt](third_party/license_texts/BSD-3-Clause.txt) |
| [`python-dotenv 1.2.3`](https://pypi.org/project/python-dotenv/1.2.3/) | `BSD-3-CLAUSE` | [NOTICES.txt](third_party/NOTICES.txt), [BSD-3-Clause.txt](third_party/license_texts/BSD-3-Clause.txt) |
| [`pyyaml 6.0.3`](https://pypi.org/project/pyyaml/6.0.3/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`realtime 2.31.0`](https://pypi.org/project/realtime/2.31.0/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`referencing 0.37.0`](https://pypi.org/project/referencing/0.37.0/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`regex 2026.9.10`](https://pypi.org/project/regex/2026.9.10/) | `APACHE-2.0 AND CNRI-PYTHON` | [NOTICES.txt](third_party/NOTICES.txt), [Apache-2.0.txt](third_party/license_texts/Apache-2.0.txt), [CNRI-Python.txt](third_party/license_texts/CNRI-Python.txt) |
| [`requests 2.34.2`](https://pypi.org/project/requests/2.34.2/) | `APACHE-2.0` | [NOTICES.txt](third_party/NOTICES.txt), [Apache-2.0.txt](third_party/license_texts/Apache-2.0.txt) |
| [`rich 15.0.0`](https://pypi.org/project/rich/15.0.0/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`rpds-py 2026.6.3`](https://pypi.org/project/rpds-py/2026.6.3/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`s3transfer 0.19.2`](https://pypi.org/project/s3transfer/0.19.2/) | `APACHE-2.0` | [NOTICES.txt](third_party/NOTICES.txt), [Apache-2.0.txt](third_party/license_texts/Apache-2.0.txt) |
| [`scantree 0.0.4`](https://pypi.org/project/scantree/0.0.4/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`shellingham 1.5.4`](https://pypi.org/project/shellingham/1.5.4/) | `ISC` | [NOTICES.txt](third_party/NOTICES.txt), [ISC.txt](third_party/license_texts/ISC.txt) |
| [`shortuuid 1.0.13`](https://pypi.org/project/shortuuid/1.0.13/) | `BSD-3-CLAUSE` | [NOTICES.txt](third_party/NOTICES.txt), [BSD-3-Clause.txt](third_party/license_texts/BSD-3-Clause.txt) |
| [`six 1.17.0`](https://pypi.org/project/six/1.17.0/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`smmap 5.0.3`](https://pypi.org/project/smmap/5.0.3/) | `BSD-3-CLAUSE` | [NOTICES.txt](third_party/NOTICES.txt), [BSD-3-Clause.txt](third_party/license_texts/BSD-3-Clause.txt) |
| [`sniffio 1.3.1`](https://pypi.org/project/sniffio/1.3.1/) | `APACHE-2.0 OR MIT` | [NOTICES.txt](third_party/NOTICES.txt), [Apache-2.0.txt](third_party/license_texts/Apache-2.0.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`sqlparse 0.6.0`](https://pypi.org/project/sqlparse/0.6.0/) | `BSD-3-CLAUSE` | [NOTICES.txt](third_party/NOTICES.txt), [BSD-3-Clause.txt](third_party/license_texts/BSD-3-Clause.txt) |
| [`starlette 1.6.0`](https://pypi.org/project/starlette/1.6.0/) | `BSD-3-CLAUSE` | [NOTICES.txt](third_party/NOTICES.txt), [BSD-3-Clause.txt](third_party/license_texts/BSD-3-Clause.txt) |
| [`storage3 2.31.0`](https://pypi.org/project/storage3/2.31.0/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`strenum 0.4.15`](https://pypi.org/project/strenum/0.4.15/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`supabase 2.31.0`](https://pypi.org/project/supabase/2.31.0/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`supabase-auth 2.31.0`](https://pypi.org/project/supabase-auth/2.31.0/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`supabase-functions 2.31.0`](https://pypi.org/project/supabase-functions/2.31.0/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`tenacity 9.1.4`](https://pypi.org/project/tenacity/9.1.4/) | `APACHE-2.0` | [NOTICES.txt](third_party/NOTICES.txt), [Apache-2.0.txt](third_party/license_texts/Apache-2.0.txt) |
| [`tiktoken 0.14.0`](https://pypi.org/project/tiktoken/0.14.0/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`tokenizers 0.23.2`](https://pypi.org/project/tokenizers/0.23.2/) | `APACHE-2.0` | [NOTICES.txt](third_party/NOTICES.txt), [Apache-2.0.txt](third_party/license_texts/Apache-2.0.txt) |
| [`toml 0.10.2`](https://pypi.org/project/toml/0.10.2/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`tqdm 4.70.1`](https://pypi.org/project/tqdm/4.70.1/) | `MIT AND MPL-2.0` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt), [MPL-2.0.txt](third_party/license_texts/MPL-2.0.txt) |
| [`trace-ingest 0.1.0`](https://github.com/NVIDIA-NeMo/labs-nemo-compass/tree/692d1bf57b6a9372f628b7c2004852aec7a9a83e/packages/trace-ingest) | `APACHE-2.0` | [NOTICES.txt](third_party/NOTICES.txt), [Apache-2.0.txt](third_party/license_texts/Apache-2.0.txt) |
| [`typer 0.27.2`](https://pypi.org/project/typer/0.27.2/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`typing-extensions 4.16.0`](https://pypi.org/project/typing-extensions/4.16.0/) | `PSF-2.0` | [NOTICES.txt](third_party/NOTICES.txt), [PSF-2.0.txt](third_party/license_texts/PSF-2.0.txt) |
| [`typing-inspection 0.4.4`](https://pypi.org/project/typing-inspection/0.4.4/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`urllib3 2.8.0`](https://pypi.org/project/urllib3/2.8.0/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
| [`uvicorn 0.53.0`](https://pypi.org/project/uvicorn/0.53.0/) | `BSD-3-CLAUSE` | [NOTICES.txt](third_party/NOTICES.txt), [BSD-3-Clause.txt](third_party/license_texts/BSD-3-Clause.txt) |
| [`websockets 15.0.1`](https://pypi.org/project/websockets/15.0.1/) | `BSD-3-CLAUSE` | [NOTICES.txt](third_party/NOTICES.txt), [BSD-3-Clause.txt](third_party/license_texts/BSD-3-Clause.txt) |
| [`yarl 1.25.1`](https://pypi.org/project/yarl/1.25.1/) | `APACHE-2.0` | [NOTICES.txt](third_party/NOTICES.txt), [Apache-2.0.txt](third_party/license_texts/Apache-2.0.txt) |
| [`zipp 4.1.0`](https://pypi.org/project/zipp/4.1.0/) | `MIT` | [NOTICES.txt](third_party/NOTICES.txt), [MIT.txt](third_party/license_texts/MIT.txt) |
