"""Reproduce the shared dependency-cache builds used by the pinned release.

These are build inputs, independent of reference, no-op and rule outcomes.
The warm-cache helper is recovered byte-for-byte from the published Dockerfiles;
its commit lists are recomputed from the pinned upstream recipes.
"""
import copy
import json
from pathlib import Path
import re
import shlex

GO_REPOS = frozenset(('caddy', 'cli', 'etcd', 'frp', 'gorm', 'hugo', 'istio',
    'lazygit', 'fzf', 'echo', 'act', 'prometheus', 'syncthing', 'go-zero', 'beego'))
PREINSTALLED_NUXT = frozenset(('4063b498d37bf732bb4f41f266bc21a5f5ceb5e9',))


def plans(recipes):
    result = {}
    for recipe in recipes.values():
        if 'error' in recipe or recipe['repo'] not in GO_REPOS | {'checkstyle'}:
            continue
        plan = result.setdefault(recipe['base'], {'kind':'maven' if recipe['repo']=='checkstyle' else 'go',
                                                  'repo':recipe['repo'], 'shas':[]})
        if plan['repo'] != recipe['repo']:
            raise ValueError('Shared cache base spans unrelated repositories')
        plan['shas'].append(recipe['base_sha'])
    return {base:{**value,'shas':sorted(set(value['shas']))} for base,value in result.items()}


def prepare_recipe(recipe, plan):
    if plan['repo'] != recipe['repo'] or recipe['base_sha'] not in plan['shas']:
        raise ValueError('Shared cache plan does not cover this source recipe')
    recipe = copy.deepcopy(recipe)
    prepare = recipe['files']['prepare.sh']
    if plan['kind'] == 'maven':
        prepare = prepare[:prepare.index('if [ ! -f ~/.m2/settings.xml ]; then')]
    else:
        prepare = re.sub(r'(?m)^go test[^\n]*\n', '', prepare)
        anchor = 'git checkout '+recipe['base_sha']+'\nbash /home/check_git_changes.sh\n'
        if prepare.count(anchor) != 1:
            raise ValueError('Unknown upstream Go checkout sequence')
        prepare = prepare.replace(anchor, anchor +
            "# Fetch only modules missing from the image's shared cache; the verifier compiles.\n"
            'if [ -f go.mod ] && [ ! -d vendor ]; then go mod download; git checkout -q -- .; fi\n'
            'test -z "$(git status --porcelain)"\n')
    recipe['files']['prepare.sh'] = prepare
    return recipe


def docker(plan):
    helper = Path(__file__).with_name('warm_cache.py').read_text()
    return ('\n# Fill one shared dependency cache for every task commit of this image.\n'
            'RUN /opt/multiswe-python/bin/python3 -c ' + shlex.quote('exec('+repr(helper)+')') +
            ' ' + shlex.quote(json.dumps(plan, sort_keys=True)) + '\n')


# Build fixes present in the published image recipes but omitted from its patch copy.
JUNIT_CONFIGURATION_CACHE_BASELINES = frozenset(('963f77559446343781cae04f9e29831eaa74f842', 'adf6d4332b8a74db3d43f90982fd87b0d5197b3c', 'b6719e2e05ea5001f25dc1628917d23d7e3e76dc', '0b10f86dd2e0a7fd232c1de032d1e2fbe312f615'))
JUNIT_LEGACY_GRGIT_BASELINES = frozenset(('733496d8767080380b1e781a3e0d88a833f0c9bc', 'f4ef4b9e607579dd0000d4bfcfe6a34b39823b53'))
LEGACY_GRGIT_DOCKER = 'RUN mkdir -p /opt/multiswe-legacy-maven/org/ajoberstar/grgit/grgit-core/4.0.1 /opt/multiswe-gradle/init.d && curl --fail --location --retry 3 https://ajoberstar.github.io/bintray-backup/org/ajoberstar/grgit/grgit-core/4.0.1/grgit-core-4.0.1.pom -o /opt/multiswe-legacy-maven/org/ajoberstar/grgit/grgit-core/4.0.1/grgit-core-4.0.1.pom && printf \'%s  %s\\n\' 85d21418d48d67e9928bb50550ce80677545d97d3bede5985c1794a2693d1d4e /opt/multiswe-legacy-maven/org/ajoberstar/grgit/grgit-core/4.0.1/grgit-core-4.0.1.pom | sha256sum --check - && curl --fail --location --retry 3 https://ajoberstar.github.io/bintray-backup/org/ajoberstar/grgit/grgit-core/4.0.1/grgit-core-4.0.1.jar -o /opt/multiswe-legacy-maven/org/ajoberstar/grgit/grgit-core/4.0.1/grgit-core-4.0.1.jar && printf \'%s  %s\\n\' 5a7d4595c1f6d150fcc18a6e5253428f114be92cb4e3f9569ac671244e072a57 /opt/multiswe-legacy-maven/org/ajoberstar/grgit/grgit-core/4.0.1/grgit-core-4.0.1.jar | sha256sum --check - && printf \'%s\\n\' \'gradle.beforeProject { p ->\' \'    if (p.name != \'"\'"\'buildSrc\'"\'"\' || p.parent != null) { return }\' \'    p.repositories.maven {\' \'        url = new File(\'"\'"\'/opt/multiswe-legacy-maven\'"\'"\').toURI();\' \'        content { includeVersion(\'"\'"\'org.ajoberstar.grgit\'"\'"\', \'"\'"\'grgit-core\'"\'"\', \'"\'"\'4.0.1\'"\'"\') }\' \'    }\' \'}\' > /opt/multiswe-gradle/init.d/multiswe-grgit.gradle\n'

def preinstall_helper(recipe, helper):
    if recipe['repo'] == 'junit5' and recipe['base_sha'] in JUNIT_CONFIGURATION_CACHE_BASELINES:
        helper = helper.replace('p.tasks.withType(org.gradle.api.tasks.testing.Test).each { t ->',
                                'p.tasks.withType(org.gradle.api.tasks.testing.Test).toList().each { t ->')
        helper = helper.replace('./gradlew --no-daemon', './gradlew --no-configuration-cache --no-daemon')
    return helper


def additional_docker(recipe):
    return LEGACY_GRGIT_DOCKER if recipe['repo'] == 'junit5' and recipe['base_sha'] in JUNIT_LEGACY_GRGIT_BASELINES else ''
