"""Preserve ET build recipes while lowering Docker syntax for Harbor Apptainer.

Task transformations, pilot selection and read-only state probes live here.
Reviewed finite entrypoint initialization runs through task setup; dependencies stay in images.
No package unions. Source-bound oracle resource/pip repairs preserve verifier assertions.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import collections
import copy
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shlex
import subprocess
import sys
import tarfile
import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

REVISION = '26ecf78458e7f756e4d06780d5fbf3dd78e91815'
SOURCE_SHA256 = '9b4d72b139e235aa64335b6a4b2511b7e710864e0f48b4e6669d2a689ca65490'
VERSION = 'et-apptainer-build-v6'
STATIC_VERSION = 'et-static-repairs-v2'
JSONSCHEMA_SOURCE_SHA256 = '14d0e5189ed8001724d4eaaee202ee56b3c40299d38a13b6d5319b7d532cc8af'
PIN_REPAIRS = json.loads(r'''{
  "evidence": [
    "pilot-validation/submissions/1941f174284d/evidence.tar.gz",
    "pin-followup-validation/submissions/0ca6f3556148/evidence.tar.gz",
    "final-pilot-validation/submissions/865a72be0dbc/evidence.tar.gz"
  ],
  "requirement": "reward-1 oracle followed by freeze --all; normal stage-4 rerun after generated pins",
  "tasks": {
    "task_000000_047019b5": {
      "script_sha256": "cde44e3f0def2f6d66ee2a14dc87c2711ee46aa2e2642fe3f25c6e907388b1ae",
      "edits": [
        {
          "requirement": "wheel",
          "start": 316,
          "end": 321,
          "original": "wheel",
          "file": "solution/solve.sh",
          "replacement": "wheel==0.48.0"
        }
      ]
    },
    "task_000000_3e17389f": {
      "script_sha256": "92a66f5cbf881225bf2bde56c499cedbebdcfbee73e42df764c6691f564741a4",
      "edits": [
        {
          "requirement": "pip",
          "start": 3771,
          "end": 3774,
          "original": "pip",
          "file": "solution/solve.sh",
          "replacement": "pip==26.2.1"
        },
        {
          "requirement": "setuptools",
          "start": 3775,
          "end": 3785,
          "original": "setuptools",
          "file": "solution/solve.sh",
          "replacement": "setuptools==84.0.0"
        },
        {
          "requirement": "wheel",
          "start": 3786,
          "end": 3791,
          "original": "wheel",
          "file": "solution/solve.sh",
          "replacement": "wheel==0.48.0"
        }
      ]
    },
    "task_000000_9aa135c8": {
      "script_sha256": "2dd3485a8ab55ba0d3609ee1acca470c2d2132ae7db305e461814aae423e73c8",
      "edits": [
        {
          "requirement": "pip",
          "start": 484,
          "end": 487,
          "original": "pip",
          "file": "solution/solve.sh",
          "replacement": "pip==26.2.1"
        },
        {
          "requirement": "setuptools",
          "start": 488,
          "end": 498,
          "original": "setuptools",
          "file": "solution/solve.sh",
          "replacement": "setuptools==84.0.0"
        },
        {
          "requirement": "requests",
          "start": 843,
          "end": 851,
          "original": "requests",
          "file": "solution/solve.sh",
          "replacement": "requests==2.34.2"
        }
      ]
    },
    "task_000000_f4935f25": {
      "script_sha256": "8286b9a57a6f152794b826a0bc3cb696a7d0938f0469997cfbe46efee6196156",
      "edits": [
        {
          "requirement": "pulp",
          "start": 65,
          "end": 69,
          "original": "pulp",
          "file": "solution/solve.sh",
          "replacement": "pulp==3.3.2"
        }
      ]
    },
    "task_000000_1bb9cf9f": {
      "script_sha256": "0ea7ab1b42e698d0add1e0053c257cb20571f19b7d2385d56cbf9e3152c1d226",
      "edits": [
        {
          "requirement": "psutil",
          "start": 1380,
          "end": 1386,
          "original": "psutil",
          "file": "solution/solve.sh",
          "replacement": "psutil==7.2.2"
        }
      ]
    },
    "task_000000_ce45e6d1": {
      "script_sha256": "da0c7278bdaaa13680cc53b7ce1ca6aba7b36f7539068844bf016cf575737e41",
      "edits": [
        {
          "requirement": "pandas",
          "start": 142,
          "end": 148,
          "original": "pandas",
          "file": "solution/solve.sh",
          "replacement": "pandas==2.3.3"
        },
        {
          "requirement": "boto3",
          "start": 149,
          "end": 154,
          "original": "boto3",
          "file": "solution/solve.sh",
          "replacement": "boto3==1.43.114"
        }
      ]
    },
    "task_000000_efb4f0f7": {
      "script_sha256": "85e83e999fb64cf7fcc6a15168be78aad684415c7a870fc0229ea1c87a08c606",
      "edits": [
        {
          "requirement": "ping3",
          "start": 191,
          "end": 196,
          "original": "ping3",
          "file": "solution/solve.sh",
          "replacement": "ping3==5.1.6"
        },
        {
          "requirement": "ping3",
          "start": 1975,
          "end": 1980,
          "original": "ping3",
          "file": "solution/solve.sh",
          "replacement": "ping3==5.1.6"
        },
        {
          "requirement": "ping3",
          "start": 3987,
          "end": 3992,
          "original": "ping3",
          "file": "solution/solve.sh",
          "replacement": "ping3==5.1.6"
        },
        {
          "requirement": "ping3",
          "start": 8612,
          "end": 8617,
          "original": "ping3",
          "file": "solution/solve.sh",
          "replacement": "ping3==5.1.6"
        },
        {
          "requirement": "ping3",
          "start": 9146,
          "end": 9151,
          "original": "ping3",
          "file": "solution/solve.sh",
          "replacement": "ping3==5.1.6"
        }
      ]
    },
    "task_000000_b5807114": {
      "script_sha256": "14d0e5189ed8001724d4eaaee202ee56b3c40299d38a13b6d5319b7d532cc8af",
      "edits": [
        {
          "requirement": "jsonschema",
          "start": 852,
          "end": 862,
          "original": "jsonschema",
          "file": "solution/solve.sh",
          "replacement": "jsonschema==4.26.0"
        },
        {
          "requirement": "jsonschema",
          "start": 3051,
          "end": 3061,
          "original": "jsonschema",
          "file": "solution/solve.sh",
          "replacement": "jsonschema==4.26.0"
        },
        {
          "requirement": "jsonschema",
          "start": 5492,
          "end": 5502,
          "original": "jsonschema",
          "file": "solution/solve.sh",
          "replacement": "jsonschema==4.26.0"
        }
      ]
    }
  }
}''')
# These are source-bound solution edits. They change resource selection only,
# preserving the commands which produce and verify the task's requested outputs.
CPU_REPAIRS = {
    'task_000000_0b92c5c3': '8985fce1b8097c9bde8c2e44f53b91ac477932586f7178f6307624927fd80928',
    'task_000000_27030ede': 'dc43dba54353cb7e997c80f815007136cee815bd89870eacf5cb8278b0e9d8fd',
    'task_000000_b3ae7cf7': '4acc4b9d5212c5b4b03ead5e11f71761a804078ece9ca05b23fede475d0aabe6',
    'task_000000_bffd5dd1': '0e13aa67639020670aa7cc5bad61b9ab9550bacaec229f61b7fbf989990871d2',
    'task_000000_bc9c5a3e': 'f1df4fc2b9bb50808d832b113a7cfbe6b99ae33b0b28f9e8aeaea74e00f1993c',
}
REVIEWED_EXCLUSIONS = {
    'task_000000_9719b9f3': {
        'source_sha256': 'ab1c146763ad2acd0476342110a1c053db29157853f4cb8a5106b2ff4c7b949f',
        'category': 'reference-solution-integrity',
        'reason': ('The source oracle installs an empty yaml.py as PyYAML 5.4.1. The recorded oracle '
                   'trajectory executes this replacement and still receives reward 1. It does not '
                   'satisfy the instruction to install the actual PyYAML package; version-only grading '
                   'accepts a placeholder. Retain the original task in the archive for verifier/reference repair.'),
    },
    'task_000000_bc9c5a3e': {
        'source_sha256': '6d68d66fb8a8b71e098ea0648200f211fab0d9c841c758bb3119b3f715f153f9',
        'category': 'reference-solution-reward-zero',
        'reason': ('On the tested Helma/Apptainer profile, exactly five ICMP requests to 8.8.8.8 '
                   'produce no latency sample. Both original and CPU-repaired references score 0: '
                   'the latency field is empty and the summary JSON is invalid. This is a '
                   'profile-specific exclusion, not a claim that the task is universally unsolvable.'),
    },
}
BASE_PATH = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
OPS = set('FROM RUN ENV WORKDIR USER LABEL CMD ENTRYPOINT'.split())
CACHE_TARGETS = {'/var/cache/apt', '/var/lib/apt', '/root/.cache/pip'}
WORKDIR_BASE_ROOT = '/home'
VERIFIER_VERSION = 'et-offline-verifier-v1'
ORIGINAL_TEST_SH_SHA256 = '52d87e5cb768634d6a7bda203b72ea77c802fdb9e8700d3d7cdcf631080c3298'
REVIEWED_REPAIRS = json.loads(r'''{
  "instructions": {
    "task_000000_3a08bb12": {
      "description_sha256": "0a129d48079da193c54a51abfc1e27f7e3aa42909712c816cb98306f185d7ed3",
      "instruction_sha256": "a5bf7db83bbb192cbd1dad89129970caa07bf5b8d899fa04b418ea57088493fd"
    },
    "task_000000_84d4748a": {
      "description_sha256": "861cdd02b2e583ae5efc110327f61556f72e7360fe7254d07e7c13b60d4c08d0",
      "instruction_sha256": "75e295224630da03eab1acd79e624c1344e1d0809eb8915b6784430dfbc53e45"
    },
    "task_000000_c40b78a6": {
      "description_sha256": "99faf2ba1dbcb1feccd02aba3071bc4a1881adaf5ef424d7021b00ebfa8a7703",
      "instruction_sha256": "83a83a509eec61b4c7658820983e8d3dc6d4016e9491ca35bac09b286da55662"
    },
    "task_000000_e24cd12d": {
      "description_sha256": "acb37e963b3ee578144105f5f588506d063b129ee3b76c0b0dcffc75ec3c8b8f",
      "instruction_sha256": "365c16bea2fa4ca234db867960f18b81bec1501b55d59f1c5f7fca2c69351516"
    },
    "task_000000_e9aac96e": {
      "description_sha256": "72ee9078822892887fae1a2c74fb95fbfe633a7cf28dc6bdb0de8ded889cb0b4",
      "instruction_sha256": "8d39c133ecfd197c4083d60fb61fe0e212556ff638675a953c7488711f6c33aa"
    }
  },
  "revision": "26ecf78458e7f756e4d06780d5fbf3dd78e91815",
  "timestamps": {
    "task_000000_44c20928": {
      "commands": [
        {
          "cwd": "/",
          "env": {
            "HOME": "/root",
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
          },
          "script": "BASE=/home/user/storage && touch -d \"3 days ago\"  ${BASE}/incoming/project1/keep.txt && touch -d \"2 days ago\"  ${BASE}/incoming/project1/large.bak && touch -d \"15 days ago\" ${BASE}/incoming/project1/old.txt && touch -d \"12 days ago\" ${BASE}/incoming/project2/archive.txt && touch -d \"1 day ago\"   ${BASE}/incoming/project2/temp2.bak",
          "source_line": 13
        }
      ],
      "source_sha256": "0831fe61449aad85038dba7f925b93b116f1004942521fcd90aea6efe64547fe"
    },
    "task_000000_5cc0f6f2": {
      "commands": [
        {
          "cwd": "/",
          "env": {
            "HOME": "/root",
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
          },
          "script": "OLD_TIMESTAMP=\"202001010000\" && touch -m -d '1 day ago' /home/user/audit_targets/webserver/newhttpd.conf && touch -m -d '1 day ago' /home/user/audit_targets/app/settings/recent.conf",
          "source_line": 58
        }
      ],
      "source_sha256": "6018318699692c694629f159c8b82a5c26ccdff6463acdcb4837d9f16218175f"
    },
    "task_000000_6723c42c": {
      "commands": [
        {
          "cwd": "/",
          "env": {
            "HOME": "/root",
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
          },
          "script": "touch -d \"1 hour ago\" /home/user/app/logs/serviceA.log",
          "source_line": 19
        },
        {
          "cwd": "/",
          "env": {
            "HOME": "/root",
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
          },
          "script": "touch -d \"25 hours ago\" /home/user/app/logs/serviceB.log",
          "source_line": 23
        },
        {
          "cwd": "/",
          "env": {
            "HOME": "/root",
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
          },
          "script": "touch -d \"9 days ago\" /home/user/app/logs/old/serviceC.log",
          "source_line": 27
        }
      ],
      "source_sha256": "63f73cf6fd9eaf323291477c17d62c747986d2175a218195a9895d4cdd10e4ad"
    },
    "task_000000_87d17935": {
      "commands": [
        {
          "cwd": "/",
          "env": {
            "HOME": "/root",
            "PATH": "/home/user/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
          },
          "script": "TEN_DAYS_AGO=$(date -d '10 days ago' '+%Y%m%d%H%M.%S') && touch -t \"$TEN_DAYS_AGO\" /home/user/experiments/exp_alpha && touch -t \"$TEN_DAYS_AGO\" /home/user/experiments/exp_alpha/model.bin && touch -t \"$TEN_DAYS_AGO\" /home/user/experiments/exp_beta && touch -t \"$TEN_DAYS_AGO\" /home/user/experiments/exp_beta/model.chk && touch -t \"$TEN_DAYS_AGO\" /home/user/experiments/exp_gamma && touch -t \"$TEN_DAYS_AGO\" /home/user/experiments/exp_gamma/metrics.json",
          "source_line": 39
        }
      ],
      "source_sha256": "77435bfa229063d1a7054961f1667d2b236c606c4c163b3693a9307d12c75b7d"
    },
    "task_000000_9ba2b0a3": {
      "commands": [
        {
          "cwd": "/",
          "env": {
            "HOME": "/root",
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
          },
          "script": "touch -d '3 days ago' /home/user/apps/logs/app1.log && touch -d '3 days ago' /home/user/apps/logs/app2.log && touch -d '5 days ago' /home/user/apps/logs/old/old.log",
          "source_line": 21
        }
      ],
      "source_sha256": "8d52db4d19f8e2df563d6eab03d5d3edbb0fd979262bd2fe0d1e38e8dd12c45d"
    },
    "task_000000_aac3d4dd": {
      "commands": [
        {
          "cwd": "/",
          "env": {
            "HOME": "/root",
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
          },
          "script": "touch -d \"30 days ago\" /home/user/projects/microservices/serviceA/scripts/deploy.sh && touch -d \"20 days ago\" /home/user/projects/microservices/serviceB/scripts/restart.sh && touch -d \"91 days ago\" /home/user/projects/microservices/serviceC/scripts/cleanup.sh",
          "source_line": 69
        }
      ],
      "source_sha256": "bd898e6a7c435932b701ff9e7007c1db81ee98294f8fbb887b0a98359054a240"
    },
    "task_000000_c0d576aa": {
      "commands": [
        {
          "cwd": "/",
          "env": {
            "HOME": "/root",
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
          },
          "script": "touch -d \"10 days ago\" /home/user/repositories/repoA/nested/sample-archive.tar.gz && touch -d \"20 days ago\" /home/user/repositories/repoB/data-1.1.tar.gz",
          "source_line": 39
        }
      ],
      "source_sha256": "29227f37b8fe593d13830d74382262900e2c008e405897f72e1f5fa139e15d42"
    },
    "task_000000_d314a7d5": {
      "commands": [
        {
          "cwd": "/",
          "env": {
            "DEBIAN_FRONTEND": "noninteractive",
            "HOME": "/root",
            "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "PYTHONIOENCODING": "utf-8"
          },
          "script": "touch -d \"2 days ago\"  /home/user/db/queries/daily/report.sql && touch -d \"10 days ago\" /home/user/db/queries/archive/old_report.sql && touch -d \"10 days ago\" /home/user/db/queries/maintenance/cleanup.sql",
          "source_line": 48
        }
      ],
      "source_sha256": "0957ec33c28b59295ec76e9433f3d658beaf855d23600b6f812fc6157389f59e"
    },
    "task_000000_d3ad8877": {
      "commands": [
        {
          "cwd": "/",
          "env": {
            "HOME": "/root",
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
          },
          "script": "touch -d '2 days ago' /home/user/configs/nginx/nginx.conf && touch -d '2 days ago' /home/user/configs/nginx/sites-available/default.conf && touch -d '2 days ago' /home/user/configs/app/app.conf && touch -d '2 days ago' /home/user/configs/app/db/database.conf && touch -d '90 days ago' /home/user/configs/obsolete/old.conf",
          "source_line": 41
        }
      ],
      "source_sha256": "c2ff26b4d8a9625e68dd55b388abd5d1b7a4fd3c7c666985bd254b4f8419833b"
    },
    "task_000000_da9fb171": {
      "commands": [
        {
          "cwd": "/",
          "env": {
            "HOME": "/root",
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
          },
          "script": "touch -d \"$(date -d '10 days ago')\" /home/user/mobile-build/logs/build-20230901.log && touch -d \"$(date -d '9 days ago')\"  /home/user/mobile-build/logs/test-20230902.log",
          "source_line": 32
        }
      ],
      "source_sha256": "96d5741a2be65d6207ac7f6f6d40b182e1d851995a458bfc7bf50074a5f3dbdd"
    },
    "task_000000_fba47f66": {
      "commands": [
        {
          "cwd": "/",
          "env": {
            "HOME": "/root",
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
          },
          "script": "BASE=/home/user/workspace && BUILDS=${BASE}/builds && ARTIFACTS=${BUILDS}/artifacts && ARCHIVE=${BUILDS}/archive && touch -d '10 days ago'         \"${ARTIFACTS}/app-core-1.0.0.jar\"         \"${ARTIFACTS}/app-core-1.1.0.jar\"         \"${ARTIFACTS}/lib-util-2.0.0.jar\"",
          "source_line": 26
        },
        {
          "cwd": "/",
          "env": {
            "HOME": "/root",
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
          },
          "script": "touch -d '1 hour ago' /home/user/workspace/builds/artifacts/fresh-feature-9999.jar /home/user/workspace/builds/artifacts/README.txt",
          "source_line": 53
        }
      ],
      "source_sha256": "68ddfd6a2462f3437a12099fd93c1f38c529fd1a0240e43c39a7852f587b40f6"
    }
  }
}''')
# Directory timestamp replay in 87d17935 regressed the archive oracle in the
# Apptainer pilot. All of its fixtures already exceed the seven-day cutoff.
# Keep its reviewed source record above, but do not apply the runtime refresh.
WITHHELD_TIMESTAMP_REPAIRS = {'task_000000_87d17935': 'directory timestamp refresh regressed the oracle pilot'}
for withheld in WITHHELD_TIMESTAMP_REPAIRS:
    REVIEWED_REPAIRS['timestamps'].pop(withheld)

VERIFIER_INSTALL = (
    '/usr/bin/python3 -m pip install --no-cache-dir --target /opt/et-verifier/uv uv==0.9.5 && '
    'UV_PYTHON_PREFERENCE=only-managed UV_PYTHON_INSTALL_DIR=/opt/et-verifier/python '
    '/opt/et-verifier/uv/bin/uv venv --no-cache --python 3.12 /opt/et-verifier/venv && '
    '/opt/et-verifier/uv/bin/uv pip install --no-cache --python '
    '/opt/et-verifier/venv/bin/python pytest==8.4.1 && '
    '/bin/rm -rf /opt/et-verifier/uv && /bin/chmod -R a+rX /opt/et-verifier\n')
OFFLINE_TEST_SH = b'''#!/bin/bash
# et-offline-verifier-v1: same isolated Python/pytest; no grading-time downloads.
mkdir -p /logs/verifier || exit $?
rm -f /logs/verifier/reward.txt /logs/verifier/reward.json || exit $?
cd /home/user || exit $?
export PATH="/opt/et-verifier/venv/bin:$PATH"
/opt/et-verifier/venv/bin/pytest /tests/test_final_state.py -v
rc=$?
rm -f /logs/verifier/reward.txt /logs/verifier/reward.json || exit $?
case "$rc" in
  0) echo 1 > /logs/verifier/reward.txt ;;
  1) echo 0 > /logs/verifier/reward.txt ;;
  *) echo "VERIFIER_ERROR pytest_exit=$rc" >&2; exit "$rc" ;;
esac
'''
PILOT = (
    'task_000000_0033979a',  # Quoted heredoc, file contents and modes.
    'task_000000_12ddbd87',  # FROM text inside a heredoc.
    'task_000000_54d91f57',  # All three BuildKit caches, mixed RUN.
    'task_000000_31275728',  # Changing WORKDIRs, git initialization.
    'task_000000_16a4ec74',  # Arithmetic expansion unsupported by the prior AST parser.
    'task_000000_25cc9764',  # PYTHONUSERBASE and explicit runtime cwd.
    'task_000000_bed13ee1',  # su, numeric ownership, SQLite initialization.
    'task_000000_13fbab8c',  # Explicit non-root USER, retained runtime limitation.
    'task_000000_c6317e51',  # Dummy curl package, preserve package and executable.
    'task_000000_155368e9',  # Exact pip version control.
)
# Reviewed finite entrypoints, bound to complete source Dockerfile bytes.
# Run their initialization with /bin/true as the payload instead of a long-lived shell.
STARTUP_REPAIRS = {
    'task_000000_0119f5d7': '9321c4079e1f8ed8ae6dc7176143d5a37ef2cd3eb1f6cff060b5856e992bdeaa',
    'task_000000_089a919e': '24898e7507f4290b6d5ddf171f1c80cdd3eb9f12794685a3d00f911c6b3d496a',
    'task_000000_1925b899': '88674712612d4764d4eb6506911d546104d303b34b4852255f3187144e89dcc6',
    'task_000000_4c58315f': '4cb548b2e208d87130ff3146744f2cca30f2ff21be6a60d3f35cbe65099391e8',
    'task_000000_c66c3f8e': '54f837cadacae997f63503989e6d57556a9d84ee9ce4b53b934208e306adc978',
    'task_000000_da9fb171': '96d5741a2be65d6207ac7f6f6d40b182e1d851995a458bfc7bf50074a5f3dbdd',
}
STARTUP_PILOT = tuple(STARTUP_REPAIRS)

# Reviewed directly against the pinned sample Dockerfiles, not their verifiers.
# These checks complement general observations, which cannot infer arbitrary
# shell/interpreter effects from static path matches.
PILOT_STATE = {
    'task_000000_0119f5d7': {'/home/user': {'mode': '0o755', 'uid': 0, 'gid': 0}},
    'task_000000_1925b899': {'/home/user': {'mode': '0o755', 'uid': 0, 'gid': 0}},
    'task_000000_089a919e': {
        '/home/user': {'mode': '0o755', 'uid': 0, 'gid': 0},
        **{f'/home/user/cloud_costs/raw_costs_{month}.csv': {
            'mode': '0o644', 'uid': 0, 'gid': 0,
            'sha256': hashlib.sha256(b'cloud_costs_placeholder\n').hexdigest(),
        } for month in ('Jan', 'Feb', 'Mar')},
        '/home/user/cloud_costs/quarterly_2023_Q1': {'absent': True},
        '/home/user/cloud_costs/permission_changes.log': {'absent': True},
    },
    'task_000000_0033979a': {
        '/home/user': {'mode': '0o777', 'uid': 0, 'gid': 0},
        '/home/user/insecure_config': {'mode': '0o755', 'uid': 0, 'gid': 0},
        '/home/user/insecure_config/sshd_config': {'mode': '0o644', 'uid': 0, 'gid': 0},
    },
    'task_000000_bed13ee1': {
        '/home/user': {'mode': '0o755', 'uid': 1000, 'gid': 1000},
        '/home/user/pipelines': {'mode': '0o770', 'uid': 1000, 'gid': 1000},
        '/home/user/pipelines/app_builds.db': {'mode': '0o660', 'uid': 1000, 'gid': 1000},
        '/tmp/init_builds.sql': {'absent': True},
    },
    'task_000000_13fbab8c': {'/home/user': {'mode': '0o755', 'uid': 1000, 'gid': 1000}},
    'task_000000_16a4ec74': {
        '/home/user': {'mode': '0o755', 'uid': 0, 'gid': 0},
        '/home/user/sample_db_data': {'mode': '0o755'},
        '/home/user/sample_db_data/customers.csv': {
            'mode': '0o755', 'sha256': hashlib.sha256(('id,name\n' + ''.join(
                f'{i},Customer_{i}\n' for i in range(1, 50))).encode()).hexdigest()},
        '/home/user/sample_db_data/orders.csv': {
            'mode': '0o755', 'sha256': hashlib.sha256(('id,customer_id,total\n' + ''.join(
                f'{i},{i % 49 + 1},{i * 10}\n' for i in range(1, 100))).encode()).hexdigest()},
        '/home/user/sample_db_data/products.csv': {
            'mode': '0o755', 'sha256': hashlib.sha256(('id,name,price\n' + ''.join(
                f'{i},Product_{i},{i * 5}\n' for i in range(1, 30))).encode()).hexdigest()},
        '/home/user/backup_benchmark': {'absent': True},
    },
    'task_000000_31275728': {
        '/home/user': {'mode': '0o755', 'uid': 0, 'gid': 0},
        '/home/user/projects/policy-docs/.git/HEAD': {
            'sha256': hashlib.sha256(b'ref: refs/heads/master\n').hexdigest()},
        '/home/user/projects/policy-docs/policy.md': {
            'sha256': hashlib.sha256(b'# Company Security Policy\n\nThis repository contains security-related policies.\nPlease read carefully and follow all rules.\n\n').hexdigest()},
    },
    'task_000000_155368e9': {'/home/user': {'mode': '0o755', 'uid': 0, 'gid': 0}},
    'task_000000_c6317e51': {
        '/usr/bin/curl': {'link': '/bin/true', 'uid': 0, 'gid': 0},
        '/home/user': {'mode': '0o777', 'uid': 0, 'gid': 0},
        '/tmp/curlpkg': {'absent': True},
    },
    # Extra cleanup control, selected with prepare-pilot --task.
    'task_000000_707e607e': {
        '/tmp/auth.log.csv': {'absent': True},
        '/tmp/network.log.csv': {'absent': True},
        '/tmp/processes.json': {'absent': True},
        '/home/user/ir_data/logs_bundle.zip': {'mode': '0o666', 'uid': 0, 'gid': 0},
    },
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


class Unsupported(ValueError):
    """Preserve and report source programs whose interpretation is unresolved."""


def heredocs(header):
    """Locate shell heredoc operators outside quotes, without parsing bodies."""
    result, quote, index = [], None, 0
    while index < len(header):
        char = header[index]
        if char == '\\' and quote != "'":
            index += 2
            continue
        if quote:
            if char == quote:
                quote = None
            index += 1
            continue
        if char in "'\"":
            quote = char
            index += 1
            continue
        if char == '#' and (index == 0 or header[index - 1].isspace()):
            break
        if header.startswith('<<<', index):
            raise Unsupported('RUN uses a here-string outside the supported Docker /bin/sh grammar')
        if header.startswith('<<', index):
            match = re.match(r'''<<(?P<tabs>-?)\s*(?P<q>['"]?)(?P<name>[A-Za-z_][A-Za-z0-9_.-]*)(?P=q)(?=\s|[;&|<>]|$)''', header[index:])
            if not match:
                raise Unsupported('Unrecognized heredoc delimiter')
            result.append({'delimiter': match['name'], 'strip_tabs': bool(match['tabs']),
                           'quoted': bool(match['q'])})
            index += len(match[0])
            continue
        index += 1
    return result


def parse_dockerfile(text):
    """Consume complete instructions before interpreting any directive."""
    lines, index, result = text.splitlines(keepends=True), 0, []
    while index < len(lines):
        start = index
        line = lines[index].rstrip('\r\n')
        index += 1
        if not line.strip() or line.lstrip().startswith('#'):
            if line.lower().lstrip().startswith('# escape=') and not line.endswith('\\'):
                raise Unsupported('Non-default Docker escape directive')
            continue
        parts = [line]
        while parts[-1].rstrip().endswith('\\'):
            parts[-1] = parts[-1].rstrip()[:-1]
            while index < len(lines):
                following = lines[index].rstrip('\r\n')
                index += 1
                if following.strip() and not following.lstrip().startswith('#'):
                    break
            else:
                raise Unsupported('Dangling Docker continuation')
            parts.append(following)
        header = ''.join(parts).strip()
        match = re.fullmatch(r'([A-Za-z]+)(?:\s+(.*))?', header)
        if not match or match[1].upper() not in OPS:
            raise Unsupported(f'Unsupported Docker instruction at line {start + 1}: {header[:100]}')
        op, value = match[1].upper(), match[2] or ''
        docs, tail = heredocs(value) if op == 'RUN' else [], []
        for doc in docs:
            body = []
            while index < len(lines):
                raw = lines[index]
                index += 1
                tail.append(raw)
                check = raw.rstrip('\r\n')
                if doc['strip_tabs']:
                    check = check.lstrip('\t')
                if check == doc['delimiter']:
                    break
                body.append(raw)
            else:
                raise Unsupported('Unclosed heredoc: ' + doc['delimiter'])
            doc['body'] = ''.join(body)
        result.append({'op': op, 'value': value, 'line': start + 1,
                       'raw': ''.join(lines[start:index]), 'tail': ''.join(tail), 'heredocs': docs})
    if not result or result[0]['op'] != 'FROM' or sum(i['op'] == 'FROM' for i in result) != 1:
        raise Unsupported('Expected exactly one initial FROM')
    if result[0]['value'] != 'ubuntu:22.04':
        raise Unsupported('Only the pinned ET Ubuntu 22.04 source is supported')
    return result


def docker_words(value, env):
    """Docker ENV quoting/expansion, with no shell evaluation or command substitution.

    The ET source uses simple $name/${name}; other parameter operators fail closed.
    All assignments in one ENV instruction expand against the preceding environment.
    """
    words, word, quote, index, active = [], '', None, 0, False
    while index < len(value):
        char = value[index]
        if char == '\\' and quote != "'":
            index += 1
            if index == len(value):
                raise Unsupported('Trailing ENV escape')
            word += value[index]
            active = True
        elif char in "'\"" and (quote is None or char == quote):
            quote = None if quote else char
            active = True
        elif char.isspace() and quote is None:
            if active:
                words.append(word)
                word, active = '', False
        elif char == '$' and quote != "'":
            match = re.match(r'\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))', value[index:])
            if match:
                word += env.get(match[1] or match[2], '')
                index += len(match[0]) - 1
            elif value[index:index + 2] == '${':
                raise Unsupported('Unsupported Docker variable expansion')
            else:
                word += char  # Docker ENV does not evaluate $(commands).
            active = True
        else:
            word += char
            active = True
        index += 1
    if quote:
        raise Unsupported('Unclosed Docker quote')
    if active:
        words.append(word)
    return words


def shell_command(script, env, cwd, keys):
    """Transport complete shell bytes through the line-oriented converter.

    RUN shells remain separate. env -u prevents final Docker ENV settings being
    injected into earlier RUNs by the existing builder. Never add shell -e to the
    source program: a source RUN's own final exit status determines success.
    """
    encoded = base64.b64encode(script.encode()).decode()
    assignments = ['-u ' + shlex.quote(key) for key in sorted(keys)]
    assignments += [shlex.quote(key + '=' + value) for key, value in sorted(env.items())]
    return (f'cd {shlex.quote(cwd)} && /usr/bin/env ' + ' '.join(assignments)
            + ' /bin/sh -c "$(/usr/bin/printf %s ' + encoded + ' | /usr/bin/base64 -d)"')


def cache_script(command, targets, root):
    """Emulate the three observed disposable cache mounts within one image build.

    Cache contents persist across mounted RUNs, but underlying image directories
    are restored after each RUN. In particular, never delete /var/lib/apt wholesale
    as a substitute for unmounting it. No shared/host cache is used.
    """
    lines = ['set -eu']
    for index, target in enumerate(targets):
        key = digest(target.encode())[:16]
        cache, backup = f'{root}/{key}.cache', f'{root}/{key}.original'
        q, c, b = map(shlex.quote, (target, cache, backup))
        lines += [f'test ! -L {q}', f'mkdir -p {shlex.quote(root)} {shlex.quote(posixpath.dirname(target))}',
                  f'if test -e {q}; then test -d {q}; mv {q} {b}; fi',
                  f'if test -d {c}; then mv {c} {q}; else mkdir -m 0755 {q}; fi']
    lines += ['set +e', '/bin/sh -c ' + shlex.quote(command), 'et_status=$?', 'set -e']
    for target in reversed(targets):
        key = digest(target.encode())[:16]
        q, c, b = map(shlex.quote, (target, f'{root}/{key}.cache', f'{root}/{key}.original'))
        lines += [f'if test -d {q}; then mv {q} {c}; fi',
                  f'if test -d {b}; then mv {b} {q}; else mkdir -p {q}; fi']
    lines.append('exit "$et_status"')
    return '\n'.join(lines) + '\n'


def split_mounts(value):
    targets = []
    while value.startswith('--'):
        option, separator, value = value.partition(' ')
        if not separator or not option.startswith('--mount='):
            raise Unsupported('Unsupported RUN option: ' + option)
        pairs = [part.split('=', 1) for part in option[8:].split(',')]
        if any(len(pair) != 2 for pair in pairs):
            raise Unsupported('Unsupported cache mount option')
        spec = dict(pairs)
        target = spec.get('target')
        if (len(spec) != len(pairs) or spec.get('type') != 'cache' or target not in CACHE_TARGETS
                or set(spec) - {'type', 'target', 'sharing'}
                or spec.get('sharing', 'shared') not in ('locked', 'shared') or target in targets):
            raise Unsupported('Unreviewed mount: ' + option)
        targets.append(target)
        value = value.lstrip()
    return value, targets


def compile_dockerfile(text):
    instructions = parse_dockerfile(text)
    env, cwd, user = {'PATH': BASE_PATH}, '/', 'root'
    keys = {'PATH', 'HOME'}
    for item in instructions:
        if item['op'] == 'ENV':
            for token in shlex.split(item['value']):
                key = token.partition('=')[0]
                if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key) or '=' not in token:
                    raise Unsupported('Only ENV key=value syntax is supported')
                keys.add(key)
    output = ['# ' + VERSION, 'FROM ubuntu:22.04']
    details = {'version': VERSION, 'runs': [], 'cache_mounts': [], 'source_sha256': digest(text.encode()),
               'runtime_review': [], 'literal_files': {}, 'probe_paths': ['/home/user']}
    cache_root = '/var/tmp/.ot-et-cache-' + details['source_sha256'][:20]
    if cache_root in text:
        raise Unsupported('Generated cache path collides with source')
    for item in instructions[1:]:
        op, value = item['op'], item['value']
        if op == 'RUN':
            if user not in ('root', '0'):
                raise Unsupported('Build-time USER requires a supported identity mapping')
            command, targets = split_mounts(value)
            if command.startswith(('[', '<<')):
                raise Unsupported('Unsupported RUN exec or standalone heredoc form')
            script = command + ('\n' + item['tail'] if item['tail'] else '')
            for path in list(details['literal_files']):
                # A later command may remove or replace a parent directory,
                # without naming the literal fixture itself (dummy curl does).
                references = [path, *(str(p) for p in PurePosixPath(path).parents if str(p) != '/')]
                references += [relative for ref in list(references)
                               if (relative := posixpath.relpath(ref, cwd)) not in ('.', '..')]
                if (any(ref in script for ref in references)
                        and not re.fullmatch(r'\s*(?:(?:chmod|chown|chgrp)\b[^;&|]*\s*(?:&&\s*)?)+', command)):
                    del details['literal_files'][path]
            syntax = subprocess.run(['/bin/sh', '-n'], input=script, text=True, capture_output=True)
            if syntax.returncode:
                raise Unsupported(f'Original RUN shell syntax at line {item["line"]}: {syntax.stderr.strip()}')
            run_env = {'HOME': '/root', **env}
            lowered = shell_command(script, run_env, cwd, keys)
            if targets:
                lowered = shell_command(cache_script(lowered, targets, cache_root),
                                        {'PATH': BASE_PATH, 'HOME': '/root'}, '/', keys)
                details['cache_mounts'].append({'line': item['line'], 'targets': targets})
            output.append("RUN /usr/bin/printf 'ET_RUN source-line=%s\\n' " + str(item['line'])
                     + ' >&2 && ' + lowered)
            details['runs'].append({'line': item['line'], 'script': script, 'script_sha256': digest(script.encode()),
                                    'cwd': cwd, 'env': dict(env), 'heredocs': len(item['heredocs'])})
            # Literal quoted fixtures provide source-derived checks independent of the compiler.
            match = re.fullmatch(r"cat\s*>\s*([A-Za-z0-9_./-]+)\s*<<(['\"])([A-Za-z0-9_.-]+)\2", command)
            if match and len(item['heredocs']) == 1:
                path = posixpath.normpath(posixpath.join(cwd, match[1]))
                details['literal_files'][path] = digest(item['heredocs'][0]['body'].encode())
                details['probe_paths'].append(path)
            details['probe_paths'] += re.findall(r'(?<![A-Za-z0-9_./-])(/(?:home/user|tmp)(?:/[A-Za-z0-9_.-]+)+)', command)
        elif op == 'ENV':
            updated = dict(token.split('=', 1) for token in docker_words(value, env))
            env.update(updated)
        elif op == 'WORKDIR':
            words = docker_words(value, env)
            if len(words) != 1 or not words[0]:
                raise Unsupported('Unresolved WORKDIR')
            cwd = posixpath.normpath(posixpath.join(cwd, words[0]))
            output.append('RUN ' + shell_command('mkdir -p ' + shlex.quote(cwd),
                                   {'HOME': '/root', **env}, '/', keys))
        elif op == 'USER':
            words = docker_words(value, env)
            if len(words) != 1:
                raise Unsupported('Unresolved USER')
            user = words[0]
            output.append('USER ' + user)
            details['runtime_review'].append('worker-ignores-USER')
        elif op in ('CMD', 'ENTRYPOINT'):
            # Preserve declarations. Exec-based Harbor startup does not implement them.
            output.append(op + ' ' + value)
            details['runtime_review'].append('startup-command-needs-review')
        elif op == 'LABEL':
            output.append(op + ' ' + value)
    if details['cache_mounts']:
        output.append('RUN /bin/rm -rf ' + shlex.quote(cache_root))
    # Resolve ENV at the Docker instruction where it was declared, not via a
    # later shell. Quoting is parsed by both Docker and the current shlex bridge.
    for key, value in sorted(env.items()):
        if '\n' in value or '\r' in value:
            raise Unsupported('Multiline runtime ENV')
        output.append(f'ENV {key}={shlex.quote(value)}')
    output.append('WORKDIR ' + cwd)
    undo = undo_precreated_workdir(cwd)
    if undo:
        output.insert(2, 'RUN ' + undo)
        details['workdir_timing'] = {'final_cwd': cwd, 'undo': undo}
    details['expected'] = {'cwd': cwd, 'user': user, 'env': env}
    details['runtime_review'].append('worker-overrides-PATH')
    if any(path.startswith('/tmp/') for path in details['probe_paths']):
        details['runtime_review'].append('tmp-bind-may-mask-image-state')
    details['runtime_review'] = sorted(set(details['runtime_review']))
    details['probe_paths'] = sorted(set(details['probe_paths']))
    details['status'] = 'build-recipe-patched-runtime-unvalidated'
    return '\n'.join(output) + '\n', details


# Image sharing is a separate transformation over the original instructions.
# The compatibility compiler remains the fallback and the comparison baseline.
SHARING_VERSION = 'et-reviewed-recipes-v4'
SHARING_STATE_DIR = '/var/lib/ot-et-image-sharing-v2'
SOURCE_CONTEXT = {'container.def', 'task.json', 'test_initial_state.py'}


def undo_precreated_workdir(cwd):
    """Undo the builder's early mkdir only below the reviewed Ubuntu /home root.

    rmdir never deletes files or nonempty directories. Source-position mkdir RUNs
    recreate the directories before they are used. Other roots are not reviewed.
    """
    if not cwd.startswith(WORKDIR_BASE_ROOT.rstrip('/') + '/'):
        return None
    paths = [str(p) for p in [PurePosixPath(cwd), *PurePosixPath(cwd).parents]
             if str(p).startswith(WORKDIR_BASE_ROOT.rstrip('/') + '/')]
    return '/bin/rmdir -- ' + ' '.join(map(shlex.quote, paths)) + ' 2>/dev/null || true'


def simple_command_prefix(value):
    """Read one flat && operand without interpreting quotes or substitutions.

    Return its original bytes, remaining bytes, and whether shell operators make
    it unsuitable for literal dependency normalization. The opaque initialization
    suffix is never reconstructed from these tokens.
    """
    quote, index, complex_shell = None, 0, False
    while index < len(value):
        char = value[index]
        if char == '\\' and quote != "'":
            index += 2
            continue
        if quote:
            if char == quote:
                quote = None
            elif quote == '"' and char in '$`':
                complex_shell = True
        elif char in "'\"":
            quote = char
        elif value.startswith('&&', index):
            return value[:index].strip(), value[index + 2:].lstrip(), complex_shell
        elif char in '$`|;<>()[*?~\n#' or char == '&':
            complex_shell = True
        index += 1
    return value.strip(), '', complex_shell or quote is not None


def normalize_apt_args(args):
    """Canonicalize a closed set of independent apt options, not package policy.

    Unknown options, config overrides, combined short flags and interspersed
    package/options retain their original argv. In particular, quiet level two
    implies assume-yes during installation and must never be erased there.
    """
    yes = {'-y', '--yes', '--assume-yes'}
    quiet = {'-q', '--quiet'}
    if args.count('update') == 1 and all(a == 'update' or a in yes | quiet for a in args):
        return ['update']
    allowed = yes | {'--no-install-recommends'}
    flags, packages, action = [], [], False
    for arg in args:
        if arg == 'install' and not action and not packages:
            action = True
        elif arg in allowed and not packages:
            flags.append('-y' if arg in yes else arg)
        elif action and re.fullmatch(r'[a-z0-9][a-z0-9+.:=-]*', arg):
            packages.append(arg)
        else:
            return args
    if not action or not packages:
        return args
    # Keep package order, pins and duplicates; only independent boolean options
    # move. Do not remove even duplicate flags, to keep the rule narrow.
    return ['install', *sorted(flags, key=lambda flag: (flag != '-y', flag)), *packages]


def dependency_operand(value):
    """Normalize literal argv and reviewed apt options; preserve shell state."""
    raw, rest, complex_shell = simple_command_prefix(value)
    if re.fullmatch(r'rm\s+-(?:rf|fr)\s+/var/lib/apt/lists/\*', raw):
        return {'command': 'rm -rf /var/lib/apt/lists/*', 'replay': False, 'installs': False, 'rest': rest}
    if complex_shell:
        return None
    try:
        words = shlex.split(raw)
    except ValueError:
        return None
    if not words:
        return None
    assignments = []
    while words and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*=[^\s]*', words[0]):
        assignments.append(words.pop(0))
    if not words:
        return None
    command, args = words[0], words[1:]
    replay, installs = False, False
    if command == 'set':
        if assignments or not args or any(not re.fullmatch(r'-[eux]+', arg) for arg in args):
            return None
        replay = True
    elif command == 'export':
        if assignments or not args or any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*=.*', arg) for arg in args):
            return None
        replay = True
    elif command in ('apt-get', 'apt'):
        if not args or not any(x in ('update', 'install', 'clean', 'autoclean') for x in args):
            return None
        if any(x in ('remove', 'purge', 'upgrade', 'dist-upgrade', 'autoremove') for x in args):
            return None
        installs = 'install' in args
    elif command in ('pip', 'pip3') or (command in ('python', 'python3') and args[:2] == ['-m', 'pip']):
        pip_args = args[2:] if command in ('python', 'python3') else args
        if not pip_args or pip_args[0] != 'install':
            return None
        # Relative/local requirements can depend on task state, unlike named packages.
        if any(x in ('-r', '--requirement', '-e', '--editable') or x.startswith(('.', '/', 'file:', 'git+'))
               or x.startswith(('-r', '--requirement=', '-e', '--editable=')) for x in pip_args[1:]):
            return None
        installs = True
    else:
        return None
    original = shlex.join([*assignments, command, *args])
    if command in ('apt-get', 'apt'):
        args = normalize_apt_args(args)
    normalized = shlex.join([*assignments, command, *args])
    return {'command': normalized, 'replay': replay, 'installs': installs, 'rest': rest,
            'normalization': {'source': original, 'normalized': normalized} if original != normalized else None}


def dependency_records(prefix, context):
    """Flatten successful literal && chains with their exact effective environment.

    No command contains expansion/substitution. set -e/-u therefore adds no failure
    cases beyond a failing operand; set -x only affects build diagnostics. Explicit
    export and inline assignments are captured without leaking into another RUN.
    """
    env = dict(context['env'])
    result = []
    for command in prefix:
        words = shlex.split(command)
        if words[0] == 'set':
            continue
        if words[0] == 'export':
            env.update(dict(word.split('=', 1) for word in words[1:]))
            continue
        local = {}
        while words and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*=.*', words[0]):
            key, value = words.pop(0).split('=', 1)
            local[key] = value
        # Preserve the one reviewed glob; quoting it would disable expansion.
        program = command if words[:2] == ['rm', '-rf'] else shlex.join(words)
        result.append(dict(context, script=program, env={**env, **local}))
    return result


def inert_startup(item):
    """Only empty, bare-shell and literal diagnostic startup declarations."""
    value = item['value']
    try:
        argv = json.loads(value) if value.startswith('[') else shlex.split(value)
    except ValueError:
        return False
    if not isinstance(argv, list) or not all(isinstance(v, str) for v in argv):
        return False
    if not argv:
        return True
    if item['op'] == 'ENTRYPOINT':
        return False
    if argv[0] in ('bash', '/bin/bash', 'sh', '/bin/sh') and argv[1:] in ([], ['-l'], ['--login']):
        return True
    return (argv[0] in ('echo', '/bin/echo') and
            not any(any(c in word for c in '$`;&|<>\n') for word in argv[1:]))


def initialization_plan(text, details, *, repaired_startup=False):
    """Separate a literal dependency prefix from complete initialization programs.

    A split inside RUN is restricted to flat && chains of literal installation,
    set and export operands. The remaining program is byte-preserved, with shell
    flags/exports replayed. No later installation or cache mount is moved to setup.
    Unresolved cases keep the compatibility-only task, with an individual reason.
    """
    if details['expected']['user'] not in ('root', '0'):
        raise Unsupported('non-root USER retained in compatibility image')
    instructions = parse_dockerfile(text)
    for item in instructions:
        if item['op'] in ('CMD', 'ENTRYPOINT') and not (repaired_startup or inert_startup(item)):
            raise Unsupported('startup declaration needs sharing-specific review')
    keys = sorted(set(details['expected']['env']) | {'HOME', 'PATH'})
    run_by_line = {r['line']: r for r in details['runs']}
    env, cwd = {'PATH': BASE_PATH}, '/'
    build, setup, boundaries, normalizations = [], [], [], []
    phase, installed = 'build', False
    for item in instructions[1:]:
        op, value = item['op'], item['value']
        if op == 'ENV':
            env.update(dict(token.split('=', 1) for token in docker_words(value, env)))
        elif op == 'WORKDIR':
            cwd = posixpath.normpath(posixpath.join(cwd, docker_words(value, env)[0]))
            record = {'script': 'mkdir -p ' + shlex.quote(cwd), 'env': {'HOME': '/root', **env},
                      'cwd': '/', 'targets': []}
            (build if phase == 'build' else setup).append(record)
        elif op == 'RUN':
            command, targets = split_mounts(value)
            run = run_by_line[item['line']]
            context = {'env': {'HOME': '/root', **run['env']}, 'cwd': run['cwd'], 'targets': targets}
            remaining, prefix, replay = command, [], []
            if phase == 'build':
                while remaining and (part := dependency_operand(remaining)) is not None:
                    prefix.append(part['command'])
                    if part.get('normalization'):
                        normalizations.append(dict(part['normalization'], line=item['line']))
                    if part['replay']:
                        replay.append(part['command'])
                    installed |= part['installs']
                    remaining = part['rest']
                if not remaining:
                    if item['tail']:
                        raise Unsupported('dependency heredoc requires review')
                    build.extend(dependency_records(prefix, context))
                    continue
            if not installed:
                raise Unsupported('initialization precedes a reviewed dependency prefix')
            if targets:
                raise Unsupported('cache-mounted mixed initialization retained in image')
            if prefix:
                build.extend(dependency_records(prefix, context))
                boundaries.append({'line': item['line'], 'dependency_operands': len(prefix), 'replay': replay})
            else:
                remaining = command
            script = (' && '.join(replay) + ' && ' if replay else '') + remaining
            if item['tail']:
                script += '\n' + item['tail']
            # Fail closed on later installers, identity operations, mount-sensitive
            # state and dynamically executed shell/interpreter initialization.
            review = re.sub(r'(?<![\w/])apt(?:-get)?\s+(?:clean|autoclean)\b', '', script)
            if re.search(r'(?<![/\w-])(?:apt(?:-get)?|pip[23]?|dpkg|npm|yarn|conda|curl|wget|make|cmake|ninja|gcc|cargo|rustup|gem|bundle|composer|uv|uvx|useradd|adduser|groupadd|addgroup|chown|chgrp|sudo|su)\b', review):
                raise Unsupported('initializer contains installation, identity or download operations')
            mounted_path = r'(?<![\w/])/(?:tmp|root|proc|sys|dev)(?:/|\b)'
            referenced_env = [value for key, value in context['env'].items()
                              if re.search(r'\$(?:' + re.escape(key) + r'\b|\{' + re.escape(key) + r'\})', script)]
            if any(re.search(mounted_path, value) for value in [script, run['cwd'], *referenced_env]):
                raise Unsupported('initializer references a runtime-mounted system path')
            if re.search(r'\b(?:python[23]?|perl|ruby|node|eval|bash|sh)\s+(?:-[^\n ]*c\b|<<|/)', remaining):
                raise Unsupported('interpreter initialization needs individual review')
            if (details['expected']['env'].get('HOME') == '/home/user'
                    and re.search(r'\bchmod\s+(?:-[^\s]*R[^\s]*\s+|--recursive\s+)[^;&\n]*\s/home/user(?:\s|$|;|&)', script)):
                raise Unsupported('recursive home permissions would touch runtime-created package caches')
            phase = 'setup'
            setup.append(dict(context, script=script))
    if not setup:
        raise Unsupported('no task initialization to separate')
    seed_home = any('/home/user' in c['script'] for c in setup) or cwd.startswith('/home/user')
    profile = {'version': SHARING_VERSION, 'base': 'ubuntu:22.04', 'build': build,
               'initialization_defaults': {'state_dir': SHARING_STATE_DIR, 'home': '/home/user' if seed_home else None},
               'runtime': details['expected'], 'environment_keys': keys}
    identity = digest(json.dumps(profile, sort_keys=True, separators=(',', ':')).encode())
    root = '/var/tmp/.ot-et-cache-' + identity[:20]
    lines = ['# ' + SHARING_VERSION, 'FROM ubuntu:22.04']
    if undo := undo_precreated_workdir(details['expected']['cwd']):
        lines.append('RUN ' + undo)
    for command in build:
        lowered = shell_command(command['script'], command['env'], command['cwd'], keys)
        if command['targets']:
            lowered = shell_command(cache_script(lowered, command['targets'], root),
                                    {'PATH': BASE_PATH, 'HOME': '/root'}, '/', keys)
        lines.append('RUN ' + lowered)
    if any(command['targets'] for command in build):
        lines.append('RUN /bin/rm -rf ' + shlex.quote(root))
    # Capture the builder's defaults rather than inheriting the runtime shell's
    # different umask. Seed the common home and preserve its mode across the
    # worker's empty-WORKDIR bind, before source initialization applies its modes.
    state = shlex.quote(SHARING_STATE_DIR)
    lines.append(f'RUN /bin/mkdir -p {state} && umask > {state}/umask')
    if seed_home:
        lines.append(f'RUN /bin/mkdir -p /home/user && /usr/bin/stat -c %a /home/user > {state}/home-mode')
    # Startup requires this cwd before setup files can be uploaded/executed.
    lines.append('RUN /bin/mkdir -p ' + shlex.quote(details['expected']['cwd']))
    for key, value in sorted(details['expected']['env'].items()):
        lines.append(f'ENV {key}={shlex.quote(value)}')
    lines.append('WORKDIR ' + details['expected']['cwd'])
    commands = ([f'/bin/chmod "$(/bin/cat {state}/home-mode)" /home/user'] if seed_home else [])
    commands += [f'umask "$(/bin/cat {state}/umask)" && ' +
                 shell_command(c['script'], c['env'], c['cwd'], keys) for c in setup]
    return {'identity': identity, 'dockerfile': '\n'.join(lines) + '\n', 'commands': commands,
            'profile': profile, 'initialization': setup, 'split_boundaries': boundaries,
            'dependency_normalizations': normalizations}


def relocate_source_context(members):
    """Keep source truth and unused recipes outside build and agent-upload paths."""
    result, moved = [], []
    names = {info.name for info, _ in members}
    for original, data in members:
        info = copy.copy(original)
        if info.name.startswith('environment/') and info.name != 'environment/Dockerfile':
            relative = info.name.removeprefix('environment/')
            if info.isdir() or relative not in SOURCE_CONTEXT:
                raise Unsupported('unreviewed extra build-context member: ' + info.name)
            info.name = 'source_metadata/environment/' + relative
            if info.name in names:
                raise Unsupported('source metadata destination already exists')
            moved.append({'source': original.name, 'destination': info.name, 'sha256': digest(data)})
        result.append((info, data))
    return result, moved


def read_task(binary):
    members, seen = [], set()
    with tarfile.open(fileobj=io.BytesIO(binary), mode='r:*') as archive:
        for info in archive:
            path = PurePosixPath(info.name)
            if path.is_absolute() or '..' in path.parts or info.name in seen:
                raise ValueError('Unsafe or duplicate task archive entry')
            seen.add(info.name)
            members.append((info, archive.extractfile(info).read() if info.isfile() else None))
    return members


def write_task(members):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w', format=tarfile.PAX_FORMAT) as archive:
        for original, data in sorted(members, key=lambda item: item[0].name):
            info = copy.copy(original)
            info.mtime, info.uid, info.gid, info.uname, info.gname = 0, 0, 0, '', ''
            info.pax_headers = {}
            if data is not None:
                info.size = len(data)
            archive.addfile(info, io.BytesIO(data) if data is not None else None)
    return buffer.getvalue()


def setup_script(commands, source_sha256):
    """Run reviewed initialization once per instance, never reset an oracle trial."""
    return ('#!/bin/bash\nset -eu\n'
            'setup_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
            'marker="$setup_dir/.ot-et-' + source_sha256 + '.done"\n'
            '[ ! -f "$marker" ] || exit 0\n' + '\n'.join('/bin/sh -c ' + shlex.quote(command) for command in commands)
            + '\n: > "$marker"\n')


def with_setup(members, commands, source_sha256):
    names = {info.name for info, _ in members}
    if any(name == 'setup_files' or name.startswith('setup_files/') for name in names):
        raise Unsupported('Existing task setup requires individual review')
    solution = next(((i, info, data) for i, (info, data) in enumerate(members)
                     if info.name == 'solution/solve.sh'), None)
    if solution is None or not solution[2]:
        raise Unsupported('Task setup requires an existing solution script')
    index, info, original = solution
    call = b'bash /setup_files/setup.sh || exit $?\n'
    if original.startswith(b'#!'):
        first, separator, rest = original.partition(b'\n')
        if not separator:
            raise Unsupported('Solution shebang has no body')
        updated = first + separator + call + rest
    else:
        updated = call + original
    members[index] = (info, updated)
    script = setup_script(commands, source_sha256).encode()
    info = tarfile.TarInfo('setup_files/setup.sh')
    info.mode = 0o755
    members.append((info, script))
    return members


def patch_task(task_id, binary, *, task_setup=True, share_images=False, offline_verifier=False,
               reviewed_repairs=True):
    members = read_task(binary)
    source = next(data for info, data in members if info.name == 'environment/Dockerfile')
    try:
        repair = STARTUP_REPAIRS.get(task_id) if task_setup else None
        if repair and digest(source) != repair:
            raise Unsupported('Reviewed startup source digest differs')
        converted, details = compile_dockerfile(source.decode())
        commands = []
        if share_images:
            try:
                plan = initialization_plan(source.decode(), details, repaired_startup=bool(repair))
                relocated, moves = relocate_source_context(members)
                if any(info.name.startswith('setup_files/') for info, _ in members):
                    raise Unsupported('existing setup requires composition review')
            except Unsupported as exc:
                details['sharing'] = {'status': 'retained', 'reason': str(exc)}
            else:
                members = relocated
                converted = plan.pop('dockerfile')
                commands.extend(plan.pop('commands'))
                details['sharing'] = dict(plan, status='separated', relocated=moves)
        timestamp = REVIEWED_REPAIRS['timestamps'].get(task_id) if reviewed_repairs and task_setup else None
        if timestamp:
            if digest(source) != timestamp['source_sha256']:
                raise Unsupported('Reviewed timestamp source digest differs')
            # Shared initialization already replays these original RUN programs.
            in_setup = details.get('sharing', {}).get('status') == 'separated'
            if not in_setup:
                keys = sorted(set(details['expected']['env']) | {'HOME', 'PATH'})
                commands.extend(shell_command(c['script'], c['env'], c['cwd'], keys)
                                for c in timestamp['commands'])
            details['timestamp_refresh'] = {'kind': 'source-initialization' if in_setup else 'reviewed-touch',
                                            'commands': timestamp['commands']}
        if repair:
            if details['expected']['user'] not in ('root', '0'):
                raise Unsupported('Task setup cannot implement a non-root runtime identity')
            entrypoints = [i['value'] for i in parse_dockerfile(source.decode()) if i['op'] == 'ENTRYPOINT']
            try:
                argv = json.loads(entrypoints[-1])
            except (IndexError, ValueError) as exc:
                raise Unsupported('Reviewed startup requires an exec-form ENTRYPOINT') from exc
            if not isinstance(argv, list) or not argv or not all(isinstance(v, str) for v in argv):
                raise Unsupported('Reviewed startup requires nonempty string arguments')
            command = shlex.join([*argv, '/bin/true'])
            commands.append(command)
            details['startup_command'] = command
        if commands:
            members = with_setup(members, commands, details['source_sha256'])
            details['task_setup'] = {'kind': 'initialization-and-startup' if details.get('sharing', {}).get('status') == 'separated'
                                    else 'reviewed-entrypoint', 'commands': len(commands),
                                    'completion_marker': '.ot-et-' + details['source_sha256'] + '.done'}
        instruction = REVIEWED_REPAIRS['instructions'].get(task_id) if reviewed_repairs else None
        if instruction:
            original = next(data for info, data in members if info.name == 'instruction.md')
            metadata = next(data for info, data in members
                            if info.name in ('environment/task.json', 'source_metadata/environment/task.json'))
            restored = json.loads(metadata)['description'].encode()
            if digest(original) != instruction['instruction_sha256'] or digest(restored) != instruction['description_sha256']:
                raise Unsupported('Reviewed instruction source digest differs')
            members = [(info, restored if info.name == 'instruction.md' else data) for info, data in members]
            details['instruction_restoration'] = dict(instruction, original_chars=len(original.decode()),
                                                      restored_chars=len(restored.decode()))
        if offline_verifier:
            wrapper = next(data for info, data in members if info.name == 'tests/test.sh')
            if digest(wrapper) != ORIGINAL_TEST_SH_SHA256:
                raise Unsupported('Reviewed verifier wrapper digest differs')
            install = shell_command(VERIFIER_INSTALL, {'HOME': '/root', 'PATH': BASE_PATH}, '/',
                                    sorted(set(details['expected']['env']) | {'HOME', 'PATH'}))
            user = details['expected']['user']
            converted += ('USER root\n' if user not in ('root', '0') else '') + 'RUN ' + install + '\n'
            if user not in ('root', '0'):
                converted += 'USER ' + user + '\n'
            members = [(info, OFFLINE_TEST_SH if info.name == 'tests/test.sh' else data) for info, data in members]
            details['offline_verifier'] = {'version': VERIFIER_VERSION, 'source_sha256': ORIGINAL_TEST_SH_SHA256,
                                           'python': '3.12', 'pytest': '8.4.1', 'uv': '0.9.5'}
    except Unsupported as exc:
        return binary, {'task_id': task_id, 'status': 'held-unchanged', 'reason': str(exc)}
    details['reviewed_state'] = PILOT_STATE.get(task_id, {})
    details['probe_paths'] = sorted(set(details['probe_paths']) | set(details['reviewed_state']))
    # Flag possible identity/ownership operations for review, without changing
    # runtime behavior or treating a static match as a host-capability failure.
    identity_ops = re.compile(r'\b(?:useradd|adduser|groupadd|addgroup|chown|chgrp|su|sudo)\b|\binstall\s+[^;&\n]*\s-[og]\b')
    if (details['expected']['user'] not in ('root', '0')
            or any(identity_ops.search(run['script']) for run in details['runs'])):
        details['runtime_review'].append('identity-and-ownership-needs-review')
    for index, (info, data) in enumerate(members):
        if info.name == 'environment/Dockerfile':
            members[index] = (info, converted.encode())
    return write_task(members), dict(details, task_id=task_id)


def rows(source):
    import pyarrow.parquet as pq
    # Dictionary decoding of task_binary is expensive with multiple Arrow threads.
    for batch in pq.ParquetFile(source).iter_batches(batch_size=16, use_threads=False):
        yield from batch.to_pylist()


def context_digest(binary):
    """The worker's full build-context hash, excluding task setup and provenance."""
    result = hashlib.sha256()
    for info, data in sorted(read_task(binary), key=lambda pair: pair[0].name):
        if info.isfile() and info.name.startswith('environment/'):
            relative = info.name.removeprefix('environment/').encode()
            result.update(len(relative).to_bytes(4, 'big'))
            result.update(relative)
            result.update(len(data).to_bytes(4, 'big'))
            result.update(data)
    return result.hexdigest()


def static_repairs(task_id, original, binary):
    """Reuse suffix normalization and apply the reviewed CPU edits, with evidence."""
    from validation.checks.instruction_suffix import suffix
    members = read_task(binary)
    files = {info.name: data for info, data in members}
    edits = {}
    instruction = suffix(files['instruction.md'], files['task.toml'])
    if instruction != files['instruction.md']:
        edits['instruction.md'] = instruction
    if task_id in PIN_REPAIRS['tasks']:
        recipe = PIN_REPAIRS['tasks'][task_id]
        source = {info.name: data for info, data in read_task(original)}['solution/solve.sh']
        if digest(source) != recipe['script_sha256']:
            raise ValueError(f'{task_id}: reviewed pip source changed')
        first, separator, body = source.partition(b'\n')
        setup_call = b'bash /setup_files/setup.sh || exit $?\n'
        if files['solution/solve.sh'] not in (source, first + separator + setup_call + body):
            raise ValueError(f'{task_id}: unexpected changes before pip repairs')
        script = files['solution/solve.sh'].decode()
        delta = len(script) - len(source.decode())
        for edit in sorted(recipe['edits'], key=lambda e: e['start'], reverse=True):
            start, end = edit['start'] + delta, edit['end'] + delta
            if script[start:end] != edit['original']:
                raise ValueError(f'{task_id}: stale package-pin evidence')
            script = script[:start] + edit['replacement'] + script[end:]
        edits['solution/solve.sh'] = script.encode()
    if task_id == 'task_000000_b5807114' and files['tests/test.sh'] == OFFLINE_TEST_SH:
        source = {info.name: data for info, data in read_task(original)}['solution/solve.sh']
        if digest(source) != JSONSCHEMA_SOURCE_SHA256:
            raise ValueError('Reviewed JSON-schema solution changed')
        # pytest is already invoked by absolute path. Its subprocess must find
        # the task Python (with jsonschema), not the isolated grading Python.
        edits['tests/test.sh'] = OFFLINE_TEST_SH.replace(b'export PATH="/opt/et-verifier/venv/bin:$PATH"\n', b'')
    if task_id in CPU_REPAIRS:
        source = {info.name: data for info, data in read_task(original)}['solution/solve.sh']
        if digest(source) != CPU_REPAIRS[task_id]:
            raise ValueError(f'{task_id}: reviewed CPU source changed')
        script = files['solution/solve.sh'].decode()
        if task_id == 'task_000000_bc9c5a3e':
            # This task measures the host; the audit must log the command used.
            script = script.replace('cpu_cmd="nproc"', 'cpu_cmd="nproc --all"')
            script = script.replace('cpu=$(nproc)', 'cpu=$(nproc --all)')
        else:
            cpus = tomllib.loads(files['task.toml'].decode())['environment']['cpus']
            if isinstance(cpus, bool) or not isinstance(cpus, int) or cpus <= 0:
                raise ValueError(f'{task_id}: expected positive integer CPU budget')
            script, count = re.subn(
                r'\$\((?:command -v nproc >/dev/null && nproc \|\| echo 4|nproc 2>/dev/null \|\| echo 4|nproc)\)',
                str(cpus), script)
            if count != (3 if task_id == 'task_000000_27030ede' else 1):
                raise ValueError(f'{task_id}: reviewed CPU replacement count changed')
        edits['solution/solve.sh'] = script.encode()
    result = [(info, edits.get(info.name, data)) for info, data in members]
    evidence = {'version': STATIC_VERSION, 'edits': [
        {'file': name, 'before_sha256': digest(files[name]), 'after_sha256': digest(data)}
        for name, data in sorted(edits.items())]}
    return write_task(result), evidence


def patch_main(args):
    import pyarrow as pa
    import pyarrow.parquet as pq
    pa.set_cpu_count(2)
    pa.set_io_thread_count(2)
    if digest(args.source.read_bytes()) != SOURCE_SHA256:
        raise ValueError('Source checksum differs from the unmodified pinned ET Parquet')
    if args.output.resolve().is_relative_to(Path(__file__).resolve().parent):
        raise ValueError('Generated artifacts must live outside data/endless_terminals')
    args.output.mkdir(parents=True, exist_ok=False)
    records, labels, reasons, dropped = [], {}, {}, {}
    target = args.output / 'tasks.parquet'
    temporary = target.with_suffix('.parquet.tmp')
    schema = pq.ParquetFile(args.source).schema_arrow
    # Plan first, so singleton recipes keep the byte-identical compatibility task.
    planned, sizes = [], collections.Counter()
    for row in rows(args.source):
        if args.filter_reviewed and row['path'] in REVIEWED_EXCLUSIONS:
            exclusion = REVIEWED_EXCLUSIONS[row['path']]
            if digest(row['task_binary']) != exclusion['source_sha256']:
                raise ValueError(f"{row['path']}: reviewed exclusion source changed")
            dropped[row['path']] = {k: exclusion[k] for k in ('category', 'reason')}
            continue
        new, details = patch_task(row['path'], row['task_binary'], task_setup=not args.no_task_setup,
                                  share_images=not args.no_share_images, offline_verifier=not args.no_offline_verifier,
                                  reviewed_repairs=not args.no_reviewed_repairs)
        sharing = details.get('sharing', {})
        if sharing.get('status') == 'separated':
            sizes[sharing['identity']] += 1
        planned.append((row, new, details))
        if len(planned) % 100 == 0:
            print(f'Planned {len(planned)}/2492', flush=True)
    image_groups = {}
    with pq.ParquetWriter(temporary, schema, compression='zstd', use_dictionary=False) as writer:
        for row, new, details in planned:
            sharing = details.get('sharing', {})
            if sharing.get('status') == 'separated' and sizes[sharing['identity']] == 1:
                identity = sharing['identity']
                new, details = patch_task(row['path'], row['task_binary'], task_setup=not args.no_task_setup,
                                          offline_verifier=not args.no_offline_verifier,
                                          reviewed_repairs=not args.no_reviewed_repairs)
                details['sharing'] = {'status': 'retained', 'reason': 'no matching dependency recipe',
                                      'candidate_identity': identity}
                sharing = details['sharing']
            if not args.no_static_repairs:
                new, details['static_repairs'] = static_repairs(row['path'], row['task_binary'], new)
            key = context_digest(new)
            details['environment_sha256'] = key
            if sharing.get('status') == 'separated':
                group = image_groups.setdefault(key, {'identity': sharing['identity'], 'tasks': [],
                                                      'profile': sharing['profile']})
                group['tasks'].append(row['path'])
            if new != row['task_binary']:
                labels[row['path']] = ['apptainer-build-compatibility']
                reasons[row['path']] = ('Preserve ordered build environment/cwd; lower heredocs and emulate '
                    'reviewed disposable cache mounts. Shared runtime behavior is unchanged.')
                if details.get('startup_command'):
                    labels[row['path']] = ['apptainer-build-and-startup-compatibility']
                    reasons[row['path']] += (' Invoke the reviewed finite source ENTRYPOINT after container mounts, '
                        'with /bin/true as payload, through guarded task setup.')
                if sharing.get('status') == 'separated':
                    labels[row['path']] = ['shared-dependency-image-and-task-setup']
                    reasons[row['path']] += (' Share a dependency execution recipe and runtime declarations after '
                        'normalizing reviewed redundant apt update flags and equivalent install option spelling/order. '
                        'Keep installation in the image; replay task initialization with its original shell context '
                        'before any repaired entrypoint. Preserve unused context metadata outside the image and '
                        'agent-visible setup. Add one guarded setup call before the unchanged solution body.')
                if details.get('workdir_timing'):
                    reasons[row['path']] += ' Undo early empty WORKDIR creation before source RUN steps.'
                if details.get('timestamp_refresh'):
                    labels[row['path']].append('fresh-instance-timestamps')
                    reasons[row['path']] += ' Refresh reviewed relative timestamps once per fresh instance, preserving source touch commands.'
                if details.get('instruction_restoration'):
                    labels[row['path']].append('restored-truncated-instruction')
                    reasons[row['path']] += ' Restore the complete original description from task metadata after source-hash review.'
                if details.get('offline_verifier'):
                    labels[row['path']].append('offline-verifier')
                    reasons[row['path']] += (' Install isolated Python 3.12/pytest 8.4.1 grading tools in the image; '
                        'preserve test assertions and task dependencies. Grading uses no bootstrap downloads; '
                        'pytest assertion failure scores zero, infrastructure/collection errors receive no reward.')
                if details.get('static_repairs'):
                    labels[row['path']].append('instruction-suffix')
                    reasons[row['path']] += ' Append the canonical timeout/anti-cheating sentence using the declared agent timeout.'
                    if row['path'] in CPU_REPAIRS:
                        labels[row['path']].append('cpu-resource-portability')
                        reasons[row['path']] += (' Bound solution workers by the declared CPU budget, or explicitly measure '
                                                'host CPUs with nproc --all and record that exact command.')
                    if row['path'] in PIN_REPAIRS['tasks']:
                        labels[row['path']].append('dependency-pinning')
                        reasons[row['path']] += ' Pin reviewed literal installs to versions captured after a reward-1 oracle, then rerun normal validation.'
                    if any(e['file'] == 'tests/test.sh' for e in details['static_repairs']['edits']):
                        labels[row['path']].append('verifier-subprocess-path')
                        reasons[row['path']] += ' Preserve the task PATH for verifier subprocesses while invoking isolated pytest by absolute path; assertions are unchanged.'
            writer.write_table(pa.Table.from_pylist([dict(row, task_binary=new)], schema=schema))
            records.append(details)
    temporary.replace(target)
    from data.utils.patch_reporting import write_patch_report
    write_patch_report(args.source, target, source={'dataset': 'obiwan96/endless-terminals',
        'url': 'https://huggingface.co/datasets/obiwan96/endless-terminals', 'revision': REVISION},
        dropped=dropped, change_labels=labels, change_reasons=reasons, patcher=__file__)
    (args.output / 'build-manifest.jsonl').write_text(''.join(json.dumps(r, sort_keys=True) + '\n' for r in records))
    save(args.output / 'image-groups.json', image_groups)
    summary = {'version': VERSION, 'sharing_version': None if args.no_share_images else SHARING_VERSION,
               'static_repairs_version': None if args.no_static_repairs else STATIC_VERSION,
               'source_sha256': SOURCE_SHA256, 'source_revision': REVISION, 'tasks': len(records),
               'reviewed_exclusions': dropped, 'source_tasks': len(records) + len(dropped),
               'statuses': dict(collections.Counter(r['status'] for r in records)),
               'runtime_validated': 0, 'task_setup': sum(bool(r.get('task_setup')) for r in records),
               'shared_tasks': sum(len(group['tasks']) for group in image_groups.values()),
               'shared_images': len(image_groups), 'unique_build_contexts': len({r['environment_sha256'] for r in records}),
               'normalized_shared_tasks': sum(bool(r.get('sharing', {}).get('dependency_normalizations')) for r in records),
               'offline_verifiers': sum(bool(r.get('offline_verifier')) for r in records),
               'timestamp_refreshes': sum(bool(r.get('timestamp_refresh')) for r in records),
               'restored_instructions': sum(bool(r.get('instruction_restoration')) for r in records),
               'workdir_timing_repairs': sum(bool(r.get('workdir_timing')) for r in records),
               'sharing_retained_reasons': dict(collections.Counter(r['sharing']['reason'] for r in records
                                                                   if r.get('sharing', {}).get('status') == 'retained')),
               'oracle_and_nop': 'not-run-by-patcher',
               'patcher_sha256': digest(Path(__file__).read_bytes()), 'output_sha256': digest(target.read_bytes())}
    save(args.output / 'summary.json', summary)
    print(json.dumps(summary, indent=2))


def pilot_main(args):
    import pyarrow as pa
    import pyarrow.parquet as pq
    args.output.mkdir(parents=True, exist_ok=False)
    selected = set(args.task or PILOT)
    kept = [row for row in rows(args.source) if row['path'] in selected]
    if {row['path'] for row in kept} != selected:
        raise ValueError('Pilot IDs missing from source')
    pq.write_table(pa.Table.from_pylist(kept), args.output / 'tasks.parquet', compression='zstd', use_dictionary=False)
    save(args.output / 'selection.json', {'source_sha256': digest(args.source.read_bytes()),
         'tasks': sorted(selected), 'purpose': 'build and initial-state pilot; no oracle/no-op'})
    print(args.output / 'tasks.parquet')


def probe_command(details):
    """Observe the actual worker shell; never repair its environment in the probe."""
    config = {'paths': details['probe_paths'], 'env': sorted(set(details['expected']['env']) | {'HOME', 'PATH'}),
              'user': details['expected']['user']}
    script = '''import hashlib,json,os,pwd,stat,subprocess
c=json.loads(CONFIG)
result={"id":subprocess.check_output(["/usr/bin/id"],text=True).strip(),"uid":os.getuid(),"gid":os.getgid(),"cwd":os.getcwd(),"env":{k:os.environ.get(k) for k in c["env"]},"paths":{}}
try: result["expected_uid"]=int(c["user"]) if c["user"].isdigit() else pwd.getpwnam(c["user"]).pw_uid
except KeyError: result["expected_uid"]=None
for path in c["paths"]:
 try:
  s=os.lstat(path); item={"mode":oct(stat.S_IMODE(s.st_mode)),"uid":s.st_uid,"gid":s.st_gid,"size":s.st_size,"link":os.readlink(path) if stat.S_ISLNK(s.st_mode) else None}
  if stat.S_ISREG(s.st_mode) and s.st_size <= 16*1024*1024:
   with open(path,"rb") as f: item["sha256"]=hashlib.file_digest(f,"sha256").hexdigest()
  result["paths"][path]=item
 except OSError as e: result["paths"][path]={"error":str(e),"errno":e.errno}
print("ET_STATE_JSON="+json.dumps(result,sort_keys=True))
'''.replace('CONFIG', repr(json.dumps(config)))
    # Jammy Python 3.10 lacks hashlib.file_digest.
    script = script.replace('hashlib.file_digest(f,"sha256").hexdigest()', 'hashlib.sha256(f.read()).hexdigest()')
    paths = ' '.join(shlex.quote(p) for p in details['probe_paths'])
    return ('/usr/bin/id; pwd; printf "PATH=%s\\n" "$PATH"; '
            '/bin/ls -la /home/user; /bin/ls -ld -- ' + paths + '; '
            '/usr/bin/python3 -c ' + shlex.quote(script))


def state_failures(details, actual):
    """Compare recorded observations with source expectations, without executing code."""
    failures = []
    if actual['uid'] != actual['expected_uid']:
        failures.append('user-mismatch')
    if actual['cwd'] != details['expected']['cwd']:
        failures.append('cwd-mismatch')
    for key, expected in details['expected']['env'].items():
        if actual['env'].get(key) != expected:
            failures.append('env-mismatch:' + key)
    for path, expected in details['literal_files'].items():
        if actual['paths'].get(path, {}).get('sha256') != expected:
            failures.append('literal-fixture-mismatch:' + path)
    for path, checks in details.get('reviewed_state', {}).items():
        observed = actual['paths'].get(path, {})
        for key, expected in checks.items():
            found = observed.get('errno') == 2 if key == 'absent' else observed.get(key)
            if found != expected:
                failures.append(f'path-{key}-mismatch:{path}')
    return failures


async def terminal_probe(env, command):
    """Observe a real Harbor interactive terminal, without its install fallback."""
    from uuid import uuid4
    from harbor.agents.terminus_2.tmux_session import TmuxSession
    name = 'et-state-' + uuid4().hex[:12]
    output = '/run/ot-harbor-tmux/' + name
    session = TmuxSession(session_name=name, environment=env)
    start = session._tmux_start_session
    launched = await env.exec(start, timeout_sec=30)
    if launched.return_code:
        return {'status': 'error', 'start_command': start, 'stderr': launched.stderr}
    script = (command + ' > ' + shlex.quote(output) + ' 2>&1; '
              '/usr/bin/touch ' + shlex.quote(output + '.done'))
    # Capture output to a private infrastructure path to avoid terminal wrapping
    # corrupting JSON. Checked task files are only read, and artifacts are removed.
    encoded = base64.b64encode(script.encode()).decode()
    line = '/bin/bash --noprofile -c "$(/usr/bin/printf %s ' + encoded + ' | /usr/bin/base64 -d)"'
    try:
        sent = await env.exec('/usr/bin/tmux send-keys -t ' + name + ' -l -- ' + shlex.quote(line)
                             + ' && /usr/bin/tmux send-keys -t ' + name + ' Enter', timeout_sec=30)
        if sent.return_code:
            return {'status': 'error', 'start_command': start, 'stderr': sent.stderr}
        for _ in range(60):
            value = await env.exec('test -f ' + shlex.quote(output + '.done')
                                   + ' && /bin/cat ' + shlex.quote(output), timeout_sec=10)
            if value.return_code == 0:
                return {'status': 'observed', 'start_command': start, 'command': command,
                        'stdout': value.stdout, 'stderr': value.stderr}
            await asyncio.sleep(0.2)
        return {'status': 'error', 'start_command': start, 'stderr': 'Terminal state probe timed out'}
    finally:
        await env.exec('/usr/bin/tmux kill-session -t ' + name + '; /bin/rm -f '
                       + shlex.quote(output) + ' ' + shlex.quote(output + '.done'), timeout_sec=30)


async def probe_main(args):
    """Add ET observations to the shared environment lifecycle in this process only."""
    from validation.stages import harbor as runtime
    from validation.stages.runner import parser
    from validation.checks import environment as inspection
    from validation.data.materialize import materialize
    args.output.mkdir(parents=True, exist_ok=False)
    tasks = materialize(args.source, args.output / 'tasks')
    records = {r['task_id']: r for r in map(json.loads, args.manifest.read_text().splitlines())}
    settings = parser().parse_args([str(args.source), '--no-fix-instruction-paths', '--no-fix-pip-pins',
                                   '--no-fix-instruction-suffix', '--submit', 'never'])
    settings.dry_run = False
    settings.environment_kwargs = {'bridge_url': args.bridge_url, 'sif_cache': str(args.images)}
    original = inspection.inspect_environment
    results = []

    async def inspect(env, task, context, label):
        ordinary = await original(env, task, context, label)
        details = records[task.paths.task_dir.name]
        if 'expected' not in details:
            return dict(ordinary, et_state={'status': 'not_checked', 'reason': details.get('reason')})
        command = probe_command(details)
        value = await env.exec(command, timeout_sec=60)
        evidence = {'command': command, 'stdout': value.stdout, 'stderr': value.stderr,
                    'return_code': value.return_code, 'expected': details['expected']}
        matches = [line.partition('=')[2] for line in (value.stdout or '').splitlines() if line.startswith('ET_STATE_JSON=')]
        failures = []
        if value.return_code or len(matches) != 1:
            failures.append('state-probe-failed')
        else:
            actual = json.loads(matches[0])
            evidence['actual'] = actual
            failures = state_failures(details, actual)
        terminal = await terminal_probe(env, command)
        lines = [line.partition('=')[2] for line in terminal.get('stdout', '').splitlines()
                 if line.startswith('ET_STATE_JSON=')]
        if terminal['status'] != 'observed' or len(lines) != 1:
            failures.append('terminal-state-probe-failed')
        else:
            terminal['actual'] = json.loads(lines[0])
            terminal['failures'] = state_failures(details, terminal['actual'])
            terminal['status'] = 'failed' if terminal['failures'] else 'passed'
            failures += ['terminal:' + f for f in terminal['failures']]
        evidence['terminal'] = terminal
        evidence.update(status='failed' if failures else 'passed', failures=failures,
                        limitations='Ownership and nonliteral file state require source-specific review; startup commands are not certified.')
        return dict(ordinary, et_state=evidence, status='failed' if failures else ordinary['status'])

    inspection.inspect_environment = inspect
    try:
        for task in tasks:
            result = await runtime.build_task(task, args.output / task.name, settings)
            results.append({'task_id': task.name, **result})
            save(args.output / 'state-probes.json', results)
            print(task.name, result['status'], flush=True)
    finally:
        inspection.inspect_environment = original
    return 0 if results and all(r['status'] == 'passed' for r in results) else 1


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ('prepare-pilot', 'probe'):
        command = sys.argv.pop(1)
    else:
        command = 'patch'
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    if command == 'patch':
        parser.add_argument('--no-share-images', action='store_true', help='Reproduce the completed compatibility-only patch')
        parser.add_argument('--no-task-setup', action='store_true', help='Build-only control for repair comparisons')
        parser.add_argument('--no-offline-verifier', action='store_true', help='Keep the original online grading wrapper')
        parser.add_argument('--no-reviewed-repairs', action='store_true', help='Keep original instructions and build-relative timestamps')
        parser.add_argument('--no-static-repairs', action='store_true', help='Reproduce historical output without suffix and reviewed CPU repairs')
        parser.add_argument('--filter-reviewed', action='store_true', help='Archive the two source-bound exclusions reviewed under the recorded Helma/Apptainer profile')
    if command == 'prepare-pilot':
        parser.add_argument('--task', action='append')
    if command == 'probe':
        parser.add_argument('--manifest', required=True, type=Path)
        parser.add_argument('--bridge-url', required=True)
        parser.add_argument('--images', required=True, type=Path)
    args = parser.parse_args()
    if command == 'patch':
        patch_main(args)
    elif command == 'prepare-pilot':
        pilot_main(args)
    else:
        raise SystemExit(asyncio.run(probe_main(args)))


if __name__ == '__main__':
    main()
