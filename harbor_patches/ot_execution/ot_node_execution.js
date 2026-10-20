'use strict';
// Execution evidence for jest, vitest and mocha verifiers, loaded through
// NODE_OPTIONS=--require. Inert unless this process is the outer test runner of
// the current verifier call; children (jest workers, nested runners) inherit the
// active marker. Counts come from the runner's own result objects (jest/vitest
// JSON results, mocha runner events), never from printed text. The record
// format is the one validation/stages/execution_evidence.py parses.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const RUNNERS = [
  ['jest', /\/(jest|jest-cli)\/bin\/jest(\.js)?$/],
  ['vitest', /\/vitest\/vitest\.mjs$/],
  ['mocha', /\/mocha\/bin\/(_?mocha|mocha\.js)$/],
];

function runnerOf(script) {
  if (!script) return null;
  let resolved = script;
  try { resolved = fs.realpathSync(script); } catch (e) { /* keep the given path */ }
  resolved = resolved.split(path.sep).join('/');
  const match = RUNNERS.find(([, pattern]) => pattern.test(resolved));
  return match ? match[0] : null;
}

// Read once: test suites may replace process.env before the END record.
const EXECUTION_LOG = process.env.OT_VERIFIER_EXECUTION_LOG;

function emit(token, kind, data) {
  // Synchronous: END is written from the 'exit' handler.
  const line = '\nOT_VERIFIER_EXECUTION:' + token + ':' + kind + ':' + JSON.stringify(data) + '\n';
  // The call's own log keeps records out of the runner output, which the task's
  // grader may parse (indentation-based mocha parsers take the last unindented
  // line as a name prefix). Without it, the output is the only place left.
  if (EXECUTION_LOG) {
    try { fs.appendFileSync(EXECUTION_LOG, line); return; } catch (e) { /* fall back to stdout */ }
  }
  fs.writeSync(1, line);
}

// jest and mocha set process.exitCode in their own 'exit' listener. On a natural
// exit, listen again from 'beforeExit' so this runs after theirs; process.exit(code)
// skips 'beforeExit' and passes its code.
function onFinalExit(callback) {
  let done = false;
  const finish = (code) => { if (!done) { done = true; callback(code); } };
  // Last resort: a runner that calls process.exit() inside its own 'exit' listener skips
  // every later listener (MiMo 000919, jest via yarn); Node's final exit call still runs.
  const reallyExit = process.reallyExit;
  if (typeof reallyExit === 'function') {
    process.reallyExit = function (code) {
      try { finish(code || process.exitCode || 0); } catch (e) { /* never block the exit */ }
      return reallyExit.apply(this, arguments);
    };
  }
  let late = false;
  process.on('beforeExit', () => {
    if (!late) {
      late = true;
      process.on('exit', (code) => finish(code || process.exitCode || 0));
    }
  });
  process.on('exit', (code) => { if (!late) finish(code || process.exitCode || 0); });
}

function optionValue(argv, name) {
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === name) return argv[i + 1];
    if (argv[i].startsWith(name + '=')) return argv[i].slice(name.length + 1);
  }
  return undefined;
}

function relative(file) {
  return path.relative(process.cwd(), file).split(path.sep).join('/');
}

// jest --json and vitest's json reporter share this result shape.
function countsFromJson(file) {
  const result = JSON.parse(fs.readFileSync(file, 'utf8'));
  const suites = result.testResults || [];
  const broken = suites.filter((s) => s.testExecError || (s.status === 'failed' && !(s.assertionResults || []).length));
  return {
    executed: (result.numPassedTests || 0) + (result.numFailedTests || 0),
    skipped: (result.numPendingTests || 0) + (result.numTodoTests || 0),
    setup_errors: 0,
    collection_errors: result.numRuntimeErrorTestSuites || broken.length,
    collection_error_files: broken.map((s) => relative(s.name || s.testFilePath || '')).sort(),
    collection_error_messages: broken.slice(0, 20).map((s) =>
      String((s.testExecError && s.testExecError.message) || s.message || s.failureMessage || '').slice(0, 500)),
  };
}

function install() {
  const token = process.env.OT_VERIFIER_EXECUTION_TOKEN;
  if (!token || process.env.OT_PYTEST_EXECUTION_ACTIVE === token) return;
  const runner = runnerOf(process.argv[1]);
  if (!runner) return;
  if (runner === 'mocha') {
    // Older bin/mocha spawns bin/_mocha: record the process that runs tests.
    hookMocha(token);
    return;
  }
  process.env.OT_PYTEST_EXECUTION_ACTIVE = token;
  const state = {version: 1, id: crypto.randomBytes(16).toString('hex'), finished: false, runner,
                 executed: 0, skipped: 0, setup_errors: 0, collection_errors: 0, exitstatus: 0};
  const args = process.argv.slice(2);
  // Keep the user's own JSON file; otherwise write ours next to the shims.
  let resultFile = optionValue(args, '--outputFile') || optionValue(args, '--outputFile.json');
  if (!resultFile) {
    resultFile = path.join(process.env.OT_VERIFIER_EXECUTION_DIR || '/tmp', 'node-results-' + state.id + '.json');
    if (runner === 'jest') {
      if (!args.includes('--json')) process.argv.push('--json');
      process.argv.push('--outputFile=' + resultFile);
    } else {
      // The default reporter keeps the console output the task expects.
      if (!args.some((a) => a.startsWith('--reporter'))) process.argv.push('--reporter=default');
      process.argv.push('--reporter=json', '--outputFile=' + resultFile);
    }
  }
  emit(token, 'BEGIN', {version: 1, id: state.id});
  onFinalExit((status) => {
    if (state.finished) return;
    try {
      // No result file: the runner stopped before it finished; END says so.
      if (fs.existsSync(resultFile)) {
        Object.assign(state, countsFromJson(resultFile));
        state.finished = true;
      }
    } catch (e) {
      state.evidence_error = String(e).slice(0, 500);
    }
    state.exitstatus = status === 0 ? 0 : 1;
    emit(token, 'END', state);
  });
}

function hookMocha(token) {
  const Module = require('module');
  const originalLoad = Module._load;
  Module._load = function (request, parent, isMain) {
    const exported = originalLoad.apply(this, arguments);
    try {
      const file = Module._resolveFilename(request, parent, isMain).split(path.sep).join('/');
      if (/\/mocha\/lib\/runner\.js$/.test(file) && exported && exported.prototype && !exported.prototype._otRecorded) {
        const run = exported.prototype.run;
        exported.prototype._otRecorded = true;
        exported.prototype.run = function () {
          const state = {version: 1, id: crypto.randomBytes(16).toString('hex'), finished: false, runner: 'mocha',
                         executed: 0, skipped: 0, setup_errors: 0, collection_errors: 0, exitstatus: 0};
          // END on the runner's own 'end' event: older mocha calls process.exit()
          // inside its 'exit' listener, so later exit listeners never run.
          const outer = process.env.OT_PYTEST_EXECUTION_ACTIVE !== token;
          if (outer) {
            process.env.OT_PYTEST_EXECUTION_ACTIVE = token;
            emit(token, 'BEGIN', {version: 1, id: state.id});
          }
          // Parallel mode re-emits worker events on this runner as well.
          this.on('pass', () => { state.executed++; });
          let failures = 0;
          this.on('fail', (test, err) => {
            failures++;
            if (test && test.type === 'hook') {
              // A failing "before all" hook stops its suite; keep where and why, like a
              // jest suite that failed to run (MiMo 001555 imports a module not written yet).
              state.setup_errors++;
              const file = test.file || (test.parent && test.parent.file) || '';
              state.collection_error_files = (state.collection_error_files || []).concat(file ? [relative(file)] : []);
              if ((state.collection_error_messages || []).length < 20) {
                state.collection_error_messages = (state.collection_error_messages || [])
                  .concat([String((err && err.message) || '').slice(0, 500)]);
              }
            } else state.executed++;
          });
          this.on('pending', () => { state.skipped++; });
          this.on('end', () => {
            if (!outer || state.finished) return;
            state.finished = true;
            state.exitstatus = failures ? 1 : 0;
            emit(token, 'END', state);
          });
          return run.apply(this, arguments);
        };
      }
    } catch (e) { /* evidence must never break the runner */ }
    return exported;
  };
}

try {
  install();
} catch (e) { /* evidence must never break the verifier */ }

module.exports = {runnerOf, countsFromJson};
