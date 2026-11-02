"""Endless repairs learned from reference, no-op and rule-check results.

Build syntax lowering and source initialization stay in patch.py. These repairs
are applied by default; patch.py --no-repairs skips them and reviewed exclusions.
Recorded hashes and edit offsets reject source drift. No audit findings are
silently repaired here.
"""
import json
import re
import tomllib

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


# Source-bound solution edits preserve the requested output, changing resource selection.
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


# Directory replay regressed this reference; its fixtures already exceed the cutoff.
WITHHELD_TIMESTAMP_REPAIRS = {'task_000000_87d17935': 'directory timestamp refresh regressed the oracle pilot'}


for withheld in WITHHELD_TIMESTAMP_REPAIRS:
    REVIEWED_REPAIRS['timestamps'].pop(withheld)


def static_repairs(task_id, original, binary):
    """Reuse suffix normalization and apply the reviewed CPU edits, with evidence."""
    from data.endless.patch import read_task, write_task, digest, OFFLINE_TEST_SH
    from validation.rules.static.instruction_suffix import suffix
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


def repair_timestamps(task_id, source, details, commands):
    from data.endless.patch import digest, Unsupported, shell_command
    timestamp = REVIEWED_REPAIRS['timestamps'].get(task_id)
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


def repair_instructions(task_id, members, details):
    from data.endless.patch import digest, Unsupported
    instruction = REVIEWED_REPAIRS['instructions'].get(task_id)
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
    return members


# Historical post-patcher path normalization from the published revision below.
# Some replacements affect archive-internal names. Reproduce them faithfully,
# without endorsing their semantics; --no-repairs omits every one for the audit.
PUBLISHED_PATH_REVISION = '578f94175bf89c2bed9f376da8045b3a9beee376'
PUBLISHED_PATH_REPAIRS = {'task_000000_04a36a2f': {'after_sha256': '2ee769b6675f4e88b9fb49d6b8da052d0ebef1616d07ee5c6d6afeb8044bc261',
                          'before_sha256': 'bb7d0a4d9b54f1db749d89573c72426607e4c33ca229c40bf3737235a393641e',
                          'edits': [{'end': 927, 'new': '/home/user/api_test/', 'old': '', 'start': 927},
                                    {'end': 963, 'new': '/home/user/api_test/', 'old': '', 'start': 963},
                                    {'end': 1000,
                                     'new': '/home/user/api_test/',
                                     'old': '',
                                     'start': 1000}]},
 'task_000000_05af8ca3': {'after_sha256': '840c202c3af3f2f2c1759edef05238482db3e7f0759529b2eab134f6f766f01f',
                          'before_sha256': '460b14c3db526522ad304166cd0e5850e8e1e2c95241d2ecbc46f710afec63ae',
                          'edits': [{'end': 588, 'new': '/home/user/', 'old': '', 'start': 588},
                                    {'end': 609, 'new': '/home/user/', 'old': '', 'start': 609},
                                    {'end': 1281, 'new': '/home/user/', 'old': '', 'start': 1281},
                                    {'end': 1300, 'new': '/home/user/', 'old': '', 'start': 1300}]},
 'task_000000_05b3fa69': {'after_sha256': 'e6061cb68c1c1fc1261d6b0fecff4c1e1e9315166bddeff83d9a56de58e153a2',
                          'before_sha256': '239000d918e5dc997b7208dd5bd75377f3d889a4582c130e0e07ff7048d8a487',
                          'edits': [{'end': 167,
                                     'new': '/home/user/my_project/',
                                     'old': '',
                                     'start': 167},
                                    {'end': 178,
                                     'new': '/home/user/my_project/',
                                     'old': '',
                                     'start': 178},
                                    {'end': 1200,
                                     'new': '/home/user/my_project/',
                                     'old': '',
                                     'start': 1200},
                                    {'end': 1211,
                                     'new': '/home/user/my_project/',
                                     'old': '',
                                     'start': 1211}]},
 'task_000000_08195dc8': {'after_sha256': '3f9a09b798d6cf1575881509e685e3625b1f10b88c3f0d93ce1f2811dd29302e',
                          'before_sha256': '0e99acbd3a0558a72cc063e84c3561546391823216fe830f8ec9fe9528bcfdd4',
                          'edits': [{'end': 2026,
                                     'new': '/home/user/provisioning/',
                                     'old': '',
                                     'start': 2026},
                                    {'end': 2138,
                                     'new': '/home/user/provisioning/',
                                     'old': '',
                                     'start': 2138}]},
 'task_000000_08c10891': {'after_sha256': '375d8d776e760fa4bab739cc4f82b82c40a57737ab7faec0a8e259df95707519',
                          'before_sha256': '644a1a62f8fff1dacbf229a9599a83eff566677dbf0e619880af190255332f4b',
                          'edits': [{'end': 3088, 'new': '/home/user/', 'old': '', 'start': 3088},
                                    {'end': 3135, 'new': '/home/user/', 'old': '', 'start': 3135},
                                    {'end': 3182, 'new': '/home/user/', 'old': '', 'start': 3182},
                                    {'end': 3229, 'new': '/home/user/', 'old': '', 'start': 3229},
                                    {'end': 3280, 'new': '/home/user/', 'old': '', 'start': 3280}]},
 'task_000000_09209ccc': {'after_sha256': '9bfff8136f03cc9b2ddebb2c16dfb0c60b6cfd287ccd4a837ade2dba35134880',
                          'before_sha256': '741401627beb2c85375232da38af726f831150461f7b730bc052523c873a57d9',
                          'edits': [{'end': 1242,
                                     'new': '/home/user/data_to_backup/',
                                     'old': '',
                                     'start': 1242},
                                    {'end': 1268,
                                     'new': '/home/user/data_to_backup/',
                                     'old': '',
                                     'start': 1268}]},
 'task_000000_15327c82': {'after_sha256': 'b5581e84d35d9470a3d0c3d1c5e5f58a1703d24b18d2a9fd8db677b35a831144',
                          'before_sha256': '960e5036aac89a07e65df0235231d60f6cf06f14e152f763839b011abe7a8e97',
                          'edits': [{'end': 1470, 'new': '/home/user/', 'old': '', 'start': 1470}]},
 'task_000000_1e7e0e2c': {'after_sha256': '53799110bd649785d189ded130f835c4e64bba1b5e09e88d28968e4c8326e1dd',
                          'before_sha256': '9ef24294b746868a16661b8a4f7bec48b0896452830fe566bc6fb1ec80ada76b',
                          'edits': [{'end': 925, 'new': '/home/user/', 'old': '', 'start': 925}]},
 'task_000000_2167b186': {'after_sha256': '8a494dbbeb6f096f86a9f34de052c6c4b1e2d9bfcb43f4081a5be10638864150',
                          'before_sha256': '9f1a351d1a9661488bada6eb9fe692fad7d2671a887e75235ee33f6580270eaa',
                          'edits': [{'end': 1624,
                                     'new': '/home/user/project-localization/',
                                     'old': '',
                                     'start': 1624}]},
 'task_000000_248cf61a': {'after_sha256': '17cf50a66e02cc4d5f28799545536ab3e27c9a1f4a9308374d4e85d9449c2a0d',
                          'before_sha256': 'd8d60e313f10382709c88b1a7a2d5b12022cbc1b81a0b92145f52ea853306a73',
                          'edits': [{'end': 1500,
                                     'new': '/home/user/project/',
                                     'old': '',
                                     'start': 1500},
                                    {'end': 1536,
                                     'new': '/home/user/project/',
                                     'old': '',
                                     'start': 1536}]},
 'task_000000_265055ef': {'after_sha256': '32afde175faef72c3cbe3b55c8ab5df1e3dfd78115ef1054e0299c8825db24dc',
                          'before_sha256': 'cb55e51a29269b392201b0ff453662708334baae611ca20085160e7d50a2bf1b',
                          'edits': [{'end': 841, 'new': '/home/user/', 'old': '', 'start': 841},
                                    {'end': 1276, 'new': '/home/user/', 'old': '', 'start': 1276}]},
 'task_000000_2d90bf11': {'after_sha256': '3f9033c52fe414f0efdead5536d2bd8fa0d081e08d3e27bab2b5bcd217cc20c0',
                          'before_sha256': '4847d00980f02b5b69baec3148d22fe898ab44f7c524aa91a746e1c8160a6aa8',
                          'edits': [{'end': 1458,
                                     'new': '/home/user/compliance_audit/',
                                     'old': '',
                                     'start': 1458},
                                    {'end': 1488,
                                     'new': '/home/user/compliance_audit/',
                                     'old': '',
                                     'start': 1488},
                                    {'end': 1518,
                                     'new': '/home/user/compliance_audit/',
                                     'old': '',
                                     'start': 1518}]},
 'task_000000_2e87a90e': {'after_sha256': '85dc49bb0172550a2e80270a881dcb4f58bf82585cc3e93f69ea597ab4619b5e',
                          'before_sha256': '26ea49ea17b475c6a8fd4d665616ee13c37f55453d003198a7915b7ba45fcfa6',
                          'edits': [{'end': 1812,
                                     'new': '/home/user/doc-project/',
                                     'old': '',
                                     'start': 1812},
                                    {'end': 1834,
                                     'new': '/home/user/doc-project/',
                                     'old': '',
                                     'start': 1834},
                                    {'end': 3675,
                                     'new': '/home/user/doc-project/',
                                     'old': '',
                                     'start': 3675},
                                    {'end': 3697,
                                     'new': '/home/user/doc-project/',
                                     'old': '',
                                     'start': 3697}]},
 'task_000000_320c4c0a': {'after_sha256': 'a96d34bcb977b4cbebe6a2bf023d88ab7b6bae4a02940cdb6f3564b543227372',
                          'before_sha256': 'a46e46f4852799f6e952a6ea8fde0c2b7b6dece36ffa1757030a2dd077650475',
                          'edits': [{'end': 2411,
                                     'new': '/home/user/deployments/staging/',
                                     'old': '',
                                     'start': 2411},
                                    {'end': 2449,
                                     'new': '/home/user/deployments/staging/',
                                     'old': '',
                                     'start': 2449}]},
 'task_000000_32af4c91': {'after_sha256': 'ff1ac28c8ba0ddf7920a3b2a6a1e0565d6b20918ae1b738d33a43f546e5f17b9',
                          'before_sha256': 'f498a57f1df052a4fe6b51d0d891529254cb273c75311c00d6fdc844009abdcf',
                          'edits': [{'end': 1746,
                                     'new': '/home/user/mobile_pipeline/',
                                     'old': '',
                                     'start': 1746}]},
 'task_000000_38093c4f': {'after_sha256': 'ee957eb6216acc83232235af1362ca2bda4ea19b74e8a09957632d037cf9c4d1',
                          'before_sha256': 'efe389e14c6c5e85c26ff8469298968525e350ce3f435eff0fc93e24e05ba648',
                          'edits': [{'end': 546,
                                     'new': '/home/user/releases/app_v2.0/',
                                     'old': '',
                                     'start': 546}]},
 'task_000000_39356494': {'after_sha256': '6c3d70bf0bb6b6c90588f4617e95c5b12f56073ad120d80326e076e54edcdc7f',
                          'before_sha256': 'cbd0b0f2d963429f51522e2f9cf5ae2abf4f27e23d21586287e9011a55db5770',
                          'edits': [{'end': 221,
                                     'new': '/home/user/my_project/',
                                     'old': '',
                                     'start': 221}]},
 'task_000000_39f31143': {'after_sha256': '82f21c7364987435471e08e76846dcdb392b5659adbb51fe4a116f30a43ab457',
                          'before_sha256': '35dedfdfc827cd9178babe95b59c8fa0359cb2a80dd3422aa7712bb32f31b708',
                          'edits': [{'end': 1235,
                                     'new': '/home/user/old_releases/',
                                     'old': '',
                                     'start': 1235},
                                    {'end': 1261,
                                     'new': '/home/user/old_releases/',
                                     'old': '',
                                     'start': 1261}]},
 'task_000000_3e128fe8': {'after_sha256': '8f292bed1c17351ccf8490b9b1337edf3f3e49da982d131f52d379dfdb192987',
                          'before_sha256': '7a8cd956c5ea17b3725a60ba3f49c811c8ec50bb20c4efc4ad0e88909c025b5b',
                          'edits': [{'end': 4226,
                                     'new': '/home/user/devsecops_policy_demo/',
                                     'old': '',
                                     'start': 4226}]},
 'task_000000_47882277': {'after_sha256': 'a12366f980ad4c91612fb6732c888d22b4d0decbaa33ae4773614a1e57633d73',
                          'before_sha256': '0865593897782dc125f8024696b190c723f311fa930c9f7a4b55d022c5bcafa1',
                          'edits': [{'end': 1336,
                                     'new': '/home/user/data_to_archive/',
                                     'old': '',
                                     'start': 1336}]},
 'task_000000_4a6bf362': {'after_sha256': '6104f536160d25ccf139c56a39656e345989ed64a8b5cfa5c196e4293d22db25',
                          'before_sha256': '4879f08d4ad503fa23c1e1db51b2a8a757ebb484ad3df186714f922014589ed7',
                          'edits': [{'end': 413,
                                     'new': '/home/user/server_logs/',
                                     'old': '',
                                     'start': 413},
                                    {'end': 426,
                                     'new': '/home/user/server_logs/',
                                     'old': '',
                                     'start': 426}]},
 'task_000000_50fdf292': {'after_sha256': '8e156f1cb59768e5db34642804144e3e2db7fd19900cc79ac48743ad9ea48b95',
                          'before_sha256': '1f5448ac5af83ff187545b1c08ddd99f40e8a1fc77a03df5cbf3bf2de67e671a',
                          'edits': [{'end': 1691, 'new': '/home/user/', 'old': '', 'start': 1691},
                                    {'end': 1734, 'new': '/home/user/', 'old': '', 'start': 1734},
                                    {'end': 1774, 'new': '/home/user/', 'old': '', 'start': 1774}]},
 'task_000000_51510fac': {'after_sha256': '9ae791375cd3940f02a790d3b153fe0a53522f87ba7198315da72898b32189cb',
                          'before_sha256': 'd002625ff9eac8f15fe4d4b328971313b06859b9d4b0827b466e6ca71d84838a',
                          'edits': [{'end': 713, 'new': '/home/user/', 'old': '', 'start': 713},
                                    {'end': 3176, 'new': '/home/user/', 'old': '', 'start': 3176}]},
 'task_000000_53f35475': {'after_sha256': '21b462286836ffde06b938423bf0a456946d8f976d9cae8e073db93389c92269',
                          'before_sha256': '9b46de8d95ec8947edb79a6794091263eb0dd3db9020c9eb6c55616b35032066',
                          'edits': [{'end': 2429,
                                     'new': '/home/user/experiments/exp001/',
                                     'old': '',
                                     'start': 2429},
                                    {'end': 2452,
                                     'new': '/home/user/experiments/exp001/',
                                     'old': '',
                                     'start': 2452}]},
 'task_000000_55cd183d': {'after_sha256': 'f173b9ce373dab2c08159488b2ad4dabfb84e6f3b85169abd08bb7ee1dea8646',
                          'before_sha256': '25348a5f9899c71190881fb06856c17933edb31addc2d78f2cd57edf41e2887c',
                          'edits': [{'end': 2596,
                                     'new': '/home/user/datasets/raw_data/',
                                     'old': '',
                                     'start': 2596},
                                    {'end': 2620,
                                     'new': '/home/user/datasets/raw_data/',
                                     'old': '',
                                     'start': 2620},
                                    {'end': 2647,
                                     'new': '/home/user/datasets/raw_data/',
                                     'old': '',
                                     'start': 2647}]},
 'task_000000_56f874ad': {'after_sha256': 'f45c605585291db7d00abbbd300c2cb99c03862e2838b7ef6afb15f9684bb2c6',
                          'before_sha256': 'ddfa197228234dc2e6201ff386e6e9d72174bbf0be2bceca123ca2606f9bd586',
                          'edits': [{'end': 1332,
                                     'new': '/home/user/project/',
                                     'old': '',
                                     'start': 1332}]},
 'task_000000_58efc061': {'after_sha256': 'd146454c10c60854efac24feea1def2a2b6a9478d930d1b0f70b58366c20d43a',
                          'before_sha256': 'ecaceecd1d46138173c5e6a9115a93744e312f41ffad1d528e91f11cbc041a82',
                          'edits': [{'end': 2563,
                                     'new': '/home/user/sync_logs/',
                                     'old': '',
                                     'start': 2563},
                                    {'end': 3467,
                                     'new': '/home/user/sync_logs/',
                                     'old': '',
                                     'start': 3467}]},
 'task_000000_5c24b67b': {'after_sha256': '7da9ddbb0febdec41fc21e7f6385e65cf4728a9ce2b5f80f8f0fe31522bd360b',
                          'before_sha256': 'b4a13aaec0cfb214c749f00c4b6ebc45ddf59da148e169bbfb6bf09ab2f63a8d',
                          'edits': [{'end': 595, 'new': '/home/user/project/', 'old': '', 'start': 595},
                                    {'end': 1289,
                                     'new': '/home/user/project/',
                                     'old': '',
                                     'start': 1289}]},
 'task_000000_6ae6c44a': {'after_sha256': '4d0a4e75d368048c033c2548a1d5aecadaf6eb66107289526f177aae33109043',
                          'before_sha256': '3efb0aea867fb51032afa2ebe933863d2c4f55b31244dfef1fa7ec06fbfa3b68',
                          'edits': [{'end': 1734,
                                     'new': '/home/user/data_backup/',
                                     'old': '',
                                     'start': 1734}]},
 'task_000000_730b23b2': {'after_sha256': 'ca764fc23949694f989cffd4274428d0a96e0cc8fd88186db86b05d7e08ca2b5',
                          'before_sha256': 'a6b7aa3e0e582dddd9fe9683f8eecebd928c67c8d8858bbfe5a0a65a855928b6',
                          'edits': [{'end': 1992,
                                     'new': '/home/user/projects/',
                                     'old': '',
                                     'start': 1992},
                                    {'end': 2046,
                                     'new': '/home/user/projects/',
                                     'old': '',
                                     'start': 2046}]},
 'task_000000_739662a4': {'after_sha256': '83ec8e8fc9a5202f9b9aa43532ace6f2263854df07aeca4437fc43a2ab662a7d',
                          'before_sha256': 'eb24d041cc7b5fe2418e487545d3ccad5aa7f41c8b493794a192888aa4de8853',
                          'edits': [{'end': 2955,
                                     'new': '/home/user/policy-as-code/',
                                     'old': '',
                                     'start': 2955}]},
 'task_000000_74eef614': {'after_sha256': 'fa90843d653baf3a48506518df76388b563231cb96e22c01ba96a8c10fd7001c',
                          'before_sha256': 'c4d101cb2e04f47cc2e0327881aa8bc3884811c0260a1d1e2f08a079334dab6f',
                          'edits': [{'end': 1729,
                                     'new': '/home/user/important_data/',
                                     'old': '',
                                     'start': 1729},
                                    {'end': 1765,
                                     'new': '/home/user/important_data/',
                                     'old': '',
                                     'start': 1765},
                                    {'end': 1801,
                                     'new': '/home/user/important_data/',
                                     'old': '',
                                     'start': 1801}]},
 'task_000000_81c4008d': {'after_sha256': 'e971a0d07775668aad7a27d5289d6a0a854cd221a2e96b15cacae178d288bc46',
                          'before_sha256': 'b60518beecda58ae2bb1b19ca0f9ed4b9c281a134ee9539675a7a0a1a6650aa6',
                          'edits': [{'end': 2711,
                                     'new': '/home/user/project/',
                                     'old': '',
                                     'start': 2711},
                                    {'end': 2931,
                                     'new': '/home/user/project/',
                                     'old': '',
                                     'start': 2931}]},
 'task_000000_82c8af2a': {'after_sha256': '4178fcbd275e7baee0ddd64912cad2ceef514a6a1ba9948098dd1781f6f1b667',
                          'before_sha256': '69ad16f9a5d609b75514b99afdfab38cb4ee7e73687609e2c36269e3a666bc80',
                          'edits': [{'end': 485,
                                     'new': '/home/user/network_configs/',
                                     'old': '',
                                     'start': 485},
                                    {'end': 525,
                                     'new': '/home/user/network_configs/',
                                     'old': '',
                                     'start': 525},
                                    {'end': 1113,
                                     'new': '/home/user/network_configs/',
                                     'old': '',
                                     'start': 1113},
                                    {'end': 1145,
                                     'new': '/home/user/network_configs/',
                                     'old': '',
                                     'start': 1145}]},
 'task_000000_873e8997': {'after_sha256': '0ee2a165aa1a346903cdbfcef45d1660ca8fbcd8b2e8b725e1cdbfab9ef11d9b',
                          'before_sha256': '284e93385be0968a29bd7362a0a04dae17de7292100cc6866dbad7ea3cfbbb63',
                          'edits': [{'end': 937, 'new': '/home/user/', 'old': '', 'start': 937}]},
 'task_000000_8af5ddca': {'after_sha256': 'c536bacec54f539a63ae586981ff2c7bc2769d8fcb1d335df94bd5594c36e241',
                          'before_sha256': '79c6debadc14c9cbb398a3454de0ab24364feb77bf35b91e6c562e61dba139a0',
                          'edits': [{'end': 918,
                                     'new': '/home/user/daily_backup/',
                                     'old': '',
                                     'start': 918},
                                    {'end': 970,
                                     'new': '/home/user/daily_backup/',
                                     'old': '',
                                     'start': 970}]},
 'task_000000_8b65b66c': {'after_sha256': '0a79f37b56eb16eaa32e29b713b9f4922a01676a0df2ac6394ce038fcd350a00',
                          'before_sha256': 'd5b8e64bd6330944131889955b6119f7a4a94a96a579f2996cd35080b83c4491',
                          'edits': [{'end': 1961,
                                     'new': '/home/user/backup/source/',
                                     'old': '',
                                     'start': 1961}]},
 'task_000000_8b7840a7': {'after_sha256': '52a55ff5b1f3fd1e255d8748af2ead133b9b73fcd882aeabf50c35102a39103d',
                          'before_sha256': '0ea1e7f10b45773b6116f6ffac9d131cc402e778f0f289795f7ac254a4196f53',
                          'edits': [{'end': 2349,
                                     'new': '/home/user/web_project/',
                                     'old': '',
                                     'start': 2349},
                                    {'end': 3336,
                                     'new': '/home/user/web_project/',
                                     'old': '',
                                     'start': 3336},
                                    {'end': 3515,
                                     'new': '/home/user/web_project/',
                                     'old': '',
                                     'start': 3515},
                                    {'end': 3633,
                                     'new': '/home/user/web_project/',
                                     'old': '',
                                     'start': 3633}]},
 'task_000000_8cd60f14': {'after_sha256': '8c6e4391ba8c7bbf53385fcb02768a06f400919ee2f59775b864c4ebaaa46fa2',
                          'before_sha256': '4e28cdba6f95d682c5bccd3e9591b7235469dbc63fb2cb6bf2aeb09934329f80',
                          'edits': [{'end': 1588, 'new': '/home/user/etl/', 'old': '', 'start': 1588},
                                    {'end': 3192, 'new': '/home/user/etl/', 'old': '', 'start': 3192}]},
 'task_000000_8d18fe5b': {'after_sha256': '068b8464feb55021c930f5014382aa489ff447cad693509fcd8504a00de789eb',
                          'before_sha256': '45c4804db6a0b16da9c5b08b729b2a256545f5fd8b7ed4814a5ebe442d6503a5',
                          'edits': [{'end': 1293, 'new': '/home/user/mlops/', 'old': '', 'start': 1293},
                                    {'end': 1642, 'new': '/home/user/mlops/', 'old': '', 'start': 1642},
                                    {'end': 2341,
                                     'new': '/home/user/mlops/',
                                     'old': '',
                                     'start': 2341}]},
 'task_000000_901b2ed3': {'after_sha256': '52ec8b4e63c436f70737c808d23567b96cde852426d7fbf05f4d6e2ec7efc05e',
                          'before_sha256': '6238c7c51568c4a7a2a979f4252d273f646fd09a0f6b469ddcbf1c2d08e375e8',
                          'edits': [{'end': 998,
                                     'new': '/home/user/certs/ssl/',
                                     'old': '',
                                     'start': 998},
                                    {'end': 1133,
                                     'new': '/home/user/certs/ssl/',
                                     'old': '',
                                     'start': 1133},
                                    {'end': 1811,
                                     'new': '/home/user/certs/ssl/',
                                     'old': '',
                                     'start': 1811}]},
 'task_000000_93f20797': {'after_sha256': '075323020c9082511755c8884d451e6d1f1924daaa00c6eb68aef8e9b1269478',
                          'before_sha256': 'fc5f53d6e3aee6e8ce4c547c6042ea3292d060a23ebe832d2c1b7842a309b69e',
                          'edits': [{'end': 702, 'new': '/home/user/', 'old': '', 'start': 702},
                                    {'end': 731, 'new': '/home/user/', 'old': '', 'start': 731}]},
 'task_000000_99906e20': {'after_sha256': '3f32e136d9004df94db9f7e7269105166e28174866e8a2bc63163e44c6c566dc',
                          'before_sha256': '3fce3f27cb7941254918d4d2a899e737d57c764dd1b573acff9e9676749c5164',
                          'edits': [{'end': 1423,
                                     'new': '/home/user/projects/widget-service/',
                                     'old': '',
                                     'start': 1423},
                                    {'end': 1584,
                                     'new': '/home/user/projects/widget-service/',
                                     'old': '',
                                     'start': 1584},
                                    {'end': 1663,
                                     'new': '/home/user/projects/widget-service/',
                                     'old': '',
                                     'start': 1663},
                                    {'end': 1784,
                                     'new': '/home/user/projects/widget-service/',
                                     'old': '',
                                     'start': 1784}]},
 'task_000000_a08fd4cd': {'after_sha256': '8ae436edc22ba42127bb7d0d1b72c17eb04c6f74ec7ee3864b1509a37373ed36',
                          'before_sha256': 'dc42152006ba7ea78e6bdeda971e6618a9fe28790bd4df9e3440a7c2c40fe26f',
                          'edits': [{'end': 889,
                                     'new': '/home/user/projects/webapp/src/',
                                     'old': '',
                                     'start': 889},
                                    {'end': 1950,
                                     'new': '/home/user/projects/webapp/src/',
                                     'old': '',
                                     'start': 1950},
                                    {'end': 2145,
                                     'new': '/home/user/projects/webapp/src/',
                                     'old': '',
                                     'start': 2145}]},
 'task_000000_a3468b18': {'after_sha256': '6a6b8b6ea8f304e095e9374bcc5b795dc2265f2f488d42154ae05f34f935c9cb',
                          'before_sha256': 'aefc2891912e862768f806f91b6fcea4e9b82ef6702fea5bc8580d70d8120f3e',
                          'edits': [{'end': 932, 'new': '/home/user/project/', 'old': '', 'start': 932},
                                    {'end': 960, 'new': '/home/user/project/', 'old': '', 'start': 960},
                                    {'end': 2503,
                                     'new': '/home/user/project/',
                                     'old': '',
                                     'start': 2503}]},
 'task_000000_a5ac3423': {'after_sha256': '89aa98591761c370fca99f7864016a2166854c24a37b2aad8a32ad6d09c8a686',
                          'before_sha256': '8ecd0a36483a7b76c41a74dd56ab63dae98e72859c5eb85dd56060397237a13f',
                          'edits': [{'end': 1069,
                                     'new': '/home/user/data/projects/',
                                     'old': '',
                                     'start': 1069},
                                    {'end': 1084,
                                     'new': '/home/user/data/projects/',
                                     'old': '',
                                     'start': 1084}]},
 'task_000000_addabd0a': {'after_sha256': 'dfe0f9bff14b6c5097726fb8db9b7d63a9e76ba64eaec03823afc2c763ddf9d5',
                          'before_sha256': 'b82d8e37aa8adadde9f71f11d7b323d439e17ab2a2b00fc261aace5a32bf5d67',
                          'edits': [{'end': 2251,
                                     'new': '/home/user/releases/shared/',
                                     'old': '',
                                     'start': 2251},
                                    {'end': 2295,
                                     'new': '/home/user/releases/shared/',
                                     'old': '',
                                     'start': 2295}]},
 'task_000000_b29cee21': {'after_sha256': '3af2d9338acee101987ae4e270a697707d329bcd48b680df31c13e1b10e13e25',
                          'before_sha256': '6091751737ed0130d53796eba3a0d8c4b6050a961f27df2be1421e5742ca8573',
                          'edits': [{'end': 1844,
                                     'new': '/home/user/workspace/project/',
                                     'old': '',
                                     'start': 1844}]},
 'task_000000_b91df6b2': {'after_sha256': 'c54d44e704e243269e992428eb270fb4d5e051714da72a76ef04962dd9c6fca1',
                          'before_sha256': '788e368ed4577098cce4ec1b52d4f8fa7e18bb8ece1b141676ca4b572ed3421d',
                          'edits': [{'end': 675,
                                     'new': '/home/user/policy_tests/',
                                     'old': '',
                                     'start': 675}]},
 'task_000000_b9fe9eec': {'after_sha256': '0c9584a74b003d68950bd61d7d4ecb600c77eff2701e52312bc98bf3c24b8ee9',
                          'before_sha256': '49736eb716f6a1ee7e042530484dc2b6578210e55172cbe3d7ba98e5a81d1662',
                          'edits': [{'end': 1699, 'new': '/home/user/app/', 'old': '', 'start': 1699}]},
 'task_000000_ba1d6ef0': {'after_sha256': '37274d8b4c74705b1be28f3a46e6367aa6fb2b9d7a679bcec88e0af71867854f',
                          'before_sha256': 'f281c12dae959c6a941ffe344ce095d50476faf00bc58526032d4b828ac8b6b2',
                          'edits': [{'end': 322,
                                     'new': '/home/user/project/',
                                     'old': '',
                                     'start': 322}]},
 'task_000000_baba95a0': {'after_sha256': 'fd39ed3996ec23e6f9afcca3c07a2e9a18091ee0ebd442fc7567fafd5b7be0e8',
                          'before_sha256': '57161f5dda4dcb8f3c933fe2b7b4fa9f47ecc2db158a9f1e1ba2f573f1651d61',
                          'edits': [{'end': 1552,
                                     'new': '/home/user/dev_project/',
                                     'old': '',
                                     'start': 1552},
                                    {'end': 1600,
                                     'new': '/home/user/dev_project/',
                                     'old': '',
                                     'start': 1600},
                                    {'end': 1618,
                                     'new': '/home/user/dev_project/',
                                     'old': '',
                                     'start': 1618}]},
 'task_000000_c3168a56': {'after_sha256': '0e55843f104a7558fe9732cbf84a9bb16ff349938ca77bda241a8498b1e1f115',
                          'before_sha256': '5f1de5030032135fdab73edcee206a9995fc32bafdd4a550dfe87707b7b351e6',
                          'edits': [{'end': 942, 'new': '/home/user/projects/', 'old': '', 'start': 942},
                                    {'end': 967, 'new': '/home/user/projects/', 'old': '', 'start': 967},
                                    {'end': 1739,
                                     'new': '/home/user/projects/',
                                     'old': '',
                                     'start': 1739},
                                    {'end': 1765,
                                     'new': '/home/user/projects/',
                                     'old': '',
                                     'start': 1765}]},
 'task_000000_c700fc39': {'after_sha256': '594a68b85df3455b02ce19ff7d96507235ce1364261b832ec9e39874b63eb2cf',
                          'before_sha256': 'ae350bd6b8fab88ee921022ffabf2e3247a4b0b53c52083e9ba1af762016558a',
                          'edits': [{'end': 729,
                                     'new': '/home/user/storage_samples/',
                                     'old': '',
                                     'start': 729}]},
 'task_000000_c83b6024': {'after_sha256': '7d63eeb8c78990aef961d7a8391e6ad35278f72943edba2cd3edb40dbe19fc0a',
                          'before_sha256': '45ab97ecacc93304773c0bf0d0958c3e0213323c21e059ce5aeb80ef3a68ec16',
                          'edits': [{'end': 3223,
                                     'new': '/home/user/incident_response/',
                                     'old': '',
                                     'start': 3223}]},
 'task_000000_cde775bd': {'after_sha256': 'e86d491573f20b2e94f97a6b409c3394ddd88e980b069ea98e0d98bf28ee4285',
                          'before_sha256': 'b068823d15f9629573087b8829209125946cd5432f8e083b4c8e450ee8159a38',
                          'edits': [{'end': 1577,
                                     'new': '/home/user/datasets/',
                                     'old': '',
                                     'start': 1577},
                                    {'end': 2402,
                                     'new': '/home/user/datasets/',
                                     'old': '',
                                     'start': 2402}]},
 'task_000000_d02e0fcd': {'after_sha256': '842889deb0d3947a2d60e9be28403f4646cc18e40c2b7bb8f760347db2767f2e',
                          'before_sha256': '404e3020ac01ca4fa7f0953318f7fecb17a557da9f98d1ad3327871f63356189',
                          'edits': [{'end': 1900,
                                     'new': '/home/user/projects/capacity-planner/',
                                     'old': '',
                                     'start': 1900}]},
 'task_000000_d3ad8877': {'after_sha256': '11a7ddd68ee90cd1a44497b79cd69c65cec67260b2ec174e08f116f45b0601b0',
                          'before_sha256': 'ad3a217a14cd20e972d1d5f70e74f137c7b35abccdf524e64f1e08767807f6ba',
                          'edits': [{'end': 500, 'new': '/home/user/configs/', 'old': '', 'start': 500},
                                    {'end': 1545,
                                     'new': '/home/user/configs/',
                                     'old': '',
                                     'start': 1545}]},
 'task_000000_d5588849': {'after_sha256': '566d0a212694376c6d9fe73eb8ec8523c0e76d2792db3599391479daef6bbb89',
                          'before_sha256': '5cee856eeca78834a8f4d66156d35714766d4e87e8b3be6f189f4e8b0963f6c9',
                          'edits': [{'end': 1263, 'new': '/home/user/', 'old': '', 'start': 1263},
                                    {'end': 1298, 'new': '/home/user/', 'old': '', 'start': 1298},
                                    {'end': 1330, 'new': '/home/user/', 'old': '', 'start': 1330},
                                    {'end': 1365, 'new': '/home/user/', 'old': '', 'start': 1365}]},
 'task_000000_d859bf49': {'after_sha256': 'e0ee0e23be40db716f1b386cd5a71b60e04f7b75bf59d2e60bd8dca2fa24f222',
                          'before_sha256': 'c6081f4f10c4009019766daeefa5ede5b5406b4ea2d0e64195873912e6180fee',
                          'edits': [{'end': 827,
                                     'new': '/home/user/data_to_backup/',
                                     'old': '',
                                     'start': 827},
                                    {'end': 847,
                                     'new': '/home/user/data_to_backup/',
                                     'old': '',
                                     'start': 847},
                                    {'end': 869,
                                     'new': '/home/user/data_to_backup/',
                                     'old': '',
                                     'start': 869},
                                    {'end': 891,
                                     'new': '/home/user/data_to_backup/',
                                     'old': '',
                                     'start': 891}]},
 'task_000000_daa1c073': {'after_sha256': '67a2a4ef9552c0f5a25a07e1acb1cfeffe3d1b789d1d62a0fc2072f778ebf1a4',
                          'before_sha256': '9be8f470f85a77ddf957af182768bf3cc8444fc70032ed4dae5e6b142611ed36',
                          'edits': [{'end': 573,
                                     'new': '/home/user/tmp_logs.v29att/',
                                     'old': '',
                                     'start': 573},
                                    {'end': 596,
                                     'new': '/home/user/tmp_logs.v29att/',
                                     'old': '',
                                     'start': 596},
                                    {'end': 619,
                                     'new': '/home/user/tmp_logs.v29att/',
                                     'old': '',
                                     'start': 619}]},
 'task_000000_dabcebde': {'after_sha256': 'c990c9bd7bac23c79e375c8fd6b00ff176d625a143c12bb4cb9d4a0de275055f',
                          'before_sha256': '6f0095cddb45512b5728aea68cfc985345393a018ee27a591796314b6cf51d83',
                          'edits': [{'end': 370,
                                     'new': '/home/user/mlops/experiments/exp123/',
                                     'old': '',
                                     'start': 370},
                                    {'end': 1534,
                                     'new': '/home/user/mlops/experiments/exp123/',
                                     'old': '',
                                     'start': 1534},
                                    {'end': 2504,
                                     'new': '/home/user/mlops/experiments/exp123/',
                                     'old': '',
                                     'start': 2504}]},
 'task_000000_e28cb093': {'after_sha256': '6385bfcfbcf4bca53a67f442dec00022b24e12bb616bfabd893b226941b65748',
                          'before_sha256': '60f6b36d0a0be08e9f243a7a88440440fd62a002e05b266e2f3432d16cddb182',
                          'edits': [{'end': 816,
                                     'new': '/home/user/incidents/',
                                     'old': '',
                                     'start': 816},
                                    {'end': 845,
                                     'new': '/home/user/incidents/',
                                     'old': '',
                                     'start': 845},
                                    {'end': 874,
                                     'new': '/home/user/incidents/',
                                     'old': '',
                                     'start': 874}]},
 'task_000000_e2d76d7e': {'after_sha256': '695e9751d3a40ec474c35007389e3d2c51d65a118f66e6c09ae8d81904cdb4d4',
                          'before_sha256': 'a4acfed2dc706b3fc4425f5eecc573825c9086386799213d45041bccd977793a',
                          'edits': [{'end': 1578, 'new': '/home/user/', 'old': '', 'start': 1578}]},
 'task_000000_f1aa08d8': {'after_sha256': '4cb8089038559ba459a1d6f07e651aebceb0fabfa417af22c008020214c5de30',
                          'before_sha256': 'e279aff3ff2dee9842064249def96988eda1aa9f50b8420a99a01a9a83d45996',
                          'edits': [{'end': 1355,
                                     'new': '/home/user/backup_source/',
                                     'old': '',
                                     'start': 1355}]},
 'task_000000_f6624c46': {'after_sha256': 'a58b389930b3ab2e5b745841c674b4ce129132680bee6bd02b9d6eb70c45ae0c',
                          'before_sha256': '0409815fd96fa5a241e27178333aa2d3f7222c6f7c165e28bb3e71caff57d783',
                          'edits': [{'end': 1383,
                                     'new': '/home/user/dashboards/',
                                     'old': '',
                                     'start': 1383}]},
 'task_000000_f81d0152': {'after_sha256': 'd9afc938922e662be230313f56a77acca3ada6105e4cc05ce9b2c55bce3dc646',
                          'before_sha256': 'a105aa9aab7099cc10b3c725204d35ecd19c5b344c6be3552e98818027363817',
                          'edits': [{'end': 973,
                                     'new': '/home/user/support-tool/',
                                     'old': '',
                                     'start': 973},
                                    {'end': 1662,
                                     'new': '/home/user/support-tool/',
                                     'old': '',
                                     'start': 1662}]},
 'task_000000_fa22c9f2': {'after_sha256': '9344b46241953755f823e21b48ccaac2e3773ce8fc3b88cea06430274665e425',
                          'before_sha256': '7af0228b25ef5bb429d8fb959710b8ce29ebc9d1e184afe134f16ccaa99507ad',
                          'edits': [{'end': 702,
                                     'new': '/home/user/project/',
                                     'old': '',
                                     'start': 702}]},
 'task_000000_fc398869': {'after_sha256': '321d39ed4f34b4fe369151e627b8f0aee0c067790f85b8876312a332a7168492',
                          'before_sha256': '9743537506f5721107fbf56292a712dafbc14d0bfaa0db5fff9f423209add81f',
                          'edits': [{'end': 695, 'new': '/home/user/logs/', 'old': '', 'start': 695},
                                    {'end': 924, 'new': '/home/user/logs/', 'old': '', 'start': 924},
                                    {'end': 1728, 'new': '/home/user/logs/', 'old': '', 'start': 1728},
                                    {'end': 1756, 'new': '/home/user/logs/', 'old': '', 'start': 1756},
                                    {'end': 1784, 'new': '/home/user/logs/', 'old': '', 'start': 1784}]},
 'task_000000_ff129e89': {'after_sha256': '9726e93e5b8be745439c7a632fdd60e9eea39c7fae15f83a18aa26659220c44c',
                          'before_sha256': '09e135341b28d58edec5d04ba97da4a84e3798f9adb63cd9a8e4b836e2378eed',
                          'edits': [{'end': 571, 'new': '/home/user/', 'old': '', 'start': 571},
                                    {'end': 605, 'new': '/home/user/', 'old': '', 'start': 605},
                                    {'end': 685, 'new': '/home/user/', 'old': '', 'start': 685},
                                    {'end': 771, 'new': '/home/user/', 'old': '', 'start': 771},
                                    {'end': 845, 'new': '/home/user/', 'old': '', 'start': 845},
                                    {'end': 1540, 'new': '/home/user/', 'old': '', 'start': 1540},
                                    {'end': 1576, 'new': '/home/user/', 'old': '', 'start': 1576},
                                    {'end': 1715, 'new': '/home/user/', 'old': '', 'start': 1715},
                                    {'end': 1852, 'new': '/home/user/', 'old': '', 'start': 1852}]},
 'task_000000_ffa6e96d': {'after_sha256': 'bb6a80c19224fe84f9b7ab99f733aaa95e7588429d15fa95959948c999b645e8',
                          'before_sha256': 'bfad4e9e6c6f6971b14e2953b4b0d27bbad2b7eb8ca5e638f0c38635da97d1d2',
                          'edits': [{'end': 2109,
                                     'new': '/home/user/backup-work/',
                                     'old': '',
                                     'start': 2109}]}}


def published_instruction_paths(task_id, binary):
    """Replay source-bound edits made after the historical patcher ran."""
    from data.endless.patch import read_task, write_task, digest
    recipe = PUBLISHED_PATH_REPAIRS.get(task_id)
    if recipe is None:
        return binary
    members = read_task(binary)
    original = next(data for member, data in members if member.name == 'instruction.md')
    if digest(original) != recipe['before_sha256']:
        raise ValueError(f'{task_id}: historical path-repair input changed')
    text = original.decode()
    for edit in reversed(recipe['edits']):
        start, end = edit['start'], edit['end']
        if text[start:end] != edit['old']:
            raise ValueError(f'{task_id}: historical path-repair offset changed')
        text = text[:start] + edit['new'] + text[end:]
    updated = text.encode()
    if digest(updated) != recipe['after_sha256']:
        raise ValueError(f'{task_id}: historical path-repair output changed')
    return write_task([(member, updated if member.name == 'instruction.md' else data)
                       for member, data in members])
