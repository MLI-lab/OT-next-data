// Execution evidence for the outer `go test` call of a verifier.
//
// Started by the bin/go shim as `ot-go-execution REAL_GO test ARGS...`. It runs
// the real `go test` with -json, prints the test output as plain text again and
// emits the BEGIN/END records that validation/stages/execution_evidence.py
// parses. Counts come from the go command's own JSON events, never from the
// text a test prints. A package whose test binary could not be built counts as
// a collection error, with the compiler's file:line messages kept as evidence.
package main

import (
	"bufio"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"regexp"
	"sort"
	"strings"
	"sync"
	"syscall"
)

type event struct {
	Action      string
	Package     string
	ImportPath  string
	Test        string
	Output      string
	FailedBuild string
}

// file.go:12:3: message, as printed by the compiler and go vet.
var compileError = regexp.MustCompile(`(?m)^\s*(\S+\.go):\d+(?::\d+)?: (.+)$`)

func emit(token, kind string, data map[string]interface{}) {
	line, _ := json.Marshal(data)
	// The call's own log keeps records out of the test output the task's grader parses.
	if log := os.Getenv("OT_VERIFIER_EXECUTION_LOG"); log != "" {
		if f, err := os.OpenFile(log, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0666); err == nil {
			_, err = fmt.Fprintf(f, "\nOT_VERIFIER_EXECUTION:%s:%s:%s\n", token, kind, line)
			if f.Close() == nil && err == nil {
				return
			}
		}
	}
	fmt.Printf("\nOT_VERIFIER_EXECUTION:%s:%s:%s\n", token, kind, line)
	os.Stdout.Sync()
}

// The user's own -json keeps their output format; otherwise -json is ours.
func wantsJSON(args []string) bool {
	for _, a := range args {
		if a == "-args" || a == "--args" {
			return false
		}
		if a == "-json" || a == "--json" || a == "-json=true" || a == "--json=true" {
			return true
		}
	}
	return false
}

func withoutVerbose(args []string) []string {
	kept := make([]string, 0, len(args))
	for i, a := range args {
		if a == "-args" || a == "--args" {
			return append(kept, args[i:]...)
		}
		switch a {
		case "-v", "--v", "-v=true", "--v=true", "-test.v", "-test.v=true":
			continue
		}
		kept = append(kept, a)
	}
	return kept
}

func main() {
	real, args := os.Args[1], os.Args[2:]
	token := os.Getenv("OT_VERIFIER_EXECUTION_TOKEN")
	userJSON := wantsJSON(args[1:])
	command := args
	if !userJSON {
		// With the user's -v the test binary prints plain text, which test2json then
		// parses: a test printing "--- PASS: TestX" would count as a test. -json alone
		// keeps go's own framing, and its output is just as verbose.
		command = append([]string{"test", "-json"}, withoutVerbose(args[1:])...)
	}
	idBytes := make([]byte, 16)
	rand.Read(idBytes)
	id := hex.EncodeToString(idBytes)
	emit(token, "BEGIN", map[string]interface{}{"version": 1, "id": id})

	cmd := exec.Command(real, command...)
	cmd.Env = append(os.Environ(), "OT_PYTEST_EXECUTION_ACTIVE="+token)
	cmd.Stdin = os.Stdin
	stdout, _ := cmd.StdoutPipe()
	stderr, _ := cmd.StderrPipe()

	executed, skipped := 0, 0
	tested := map[string]bool{}
	started := map[string]bool{}
	failedBuild := map[string]bool{}
	setupFailed := map[string]bool{}
	var buildText strings.Builder
	var mu sync.Mutex
	keep := func(text string) {
		mu.Lock()
		if buildText.Len() < 1<<20 {
			buildText.WriteString(text)
		}
		mu.Unlock()
	}

	if err := cmd.Start(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		emit(token, "END", map[string]interface{}{"version": 1, "id": id, "finished": true, "runner": "go test",
			"executed": 0, "skipped": 0, "setup_errors": 0, "collection_errors": 0, "exitstatus": 4})
		os.Exit(1)
	}
	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		// Older go versions print build failures as text on stderr.
		defer wg.Done()
		reader := bufio.NewReader(stderr)
		for {
			line, err := reader.ReadString('\n')
			if line != "" {
				os.Stderr.WriteString(line)
				keep(line)
			}
			if err != nil {
				return
			}
		}
	}()
	reader := bufio.NewReaderSize(stdout, 1<<20)
	for {
		line, err := reader.ReadString('\n')
		if line != "" {
			var e event
			if strings.HasPrefix(line, "{") && json.Unmarshal([]byte(line), &e) == nil && e.Action != "" {
				if userJSON {
					os.Stdout.WriteString(line)
				} else if e.Action == "output" || e.Action == "build-output" {
					os.Stdout.WriteString(e.Output)
				}
				pkg := e.Package
				switch {
				case e.Action == "build-output":
					keep(e.Output)
				case e.Test != "" && !strings.Contains(e.Test, "/"):
					// Top-level tests only; subtests belong to their parent. Only tests
					// go reported as started count, never a bare result line.
					key := pkg + "\x00" + e.Test
					switch e.Action {
					case "run":
						started[key] = true
					case "pass", "fail":
						if started[key] {
							executed++
							tested[pkg] = true
						}
					case "skip":
						if started[key] {
							skipped++
							tested[pkg] = true
						}
					}
				case e.Test == "" && e.Action == "output" && strings.Contains(e.Output, "[build failed]"):
					// Written by the go command itself: the test binary never started.
					failedBuild[pkg] = true
				case e.Test == "" && e.Action == "output" && strings.Contains(e.Output, "[setup failed]"):
					// Package loading failed (missing module, bad go.sum): not a compile error.
					setupFailed[pkg] = true
				case e.Test == "" && e.Action == "fail" && e.FailedBuild != "":
					failedBuild[pkg] = true
				}
			} else {
				os.Stdout.WriteString(line)
			}
		}
		if err == io.EOF || (err != nil && line == "") {
			break
		}
	}
	wg.Wait()
	status := 0
	if err := cmd.Wait(); err != nil {
		status = 1
		if exit, ok := err.(*exec.ExitError); ok {
			// WaitStatus rather than ExitCode(): go1.11 images build this too.
			if ws, ok := exit.Sys().(syscall.WaitStatus); ok {
				if ws.Signaled() {
					status = 128 + int(ws.Signal())
				} else {
					status = ws.ExitStatus()
				}
			}
		}
	}
	files := map[string]bool{}
	var messages []string
	for _, m := range compileError.FindAllStringSubmatch(buildText.String(), -1) {
		files[m[1]] = true
		if len(messages) < 20 {
			messages = append(messages, m[1]+": "+m[2])
		}
	}
	errorFiles := make([]string, 0, len(files))
	for f := range files {
		errorFiles = append(errorFiles, f)
	}
	sort.Strings(errorFiles)
	// pytest-shaped exit status: 0 all passed, 1 some failed, 4 usage error.
	pytestStatus := 0
	if status != 0 {
		pytestStatus = 1
		if status == 2 && executed == 0 && len(failedBuild)+len(setupFailed) == 0 {
			pytestStatus = 4
		}
	}
	finished := status < 128
	emit(token, "END", map[string]interface{}{"version": 1, "id": id, "finished": finished, "runner": "go test",
		"executed": executed, "skipped": skipped, "setup_errors": 0,
		"collection_errors": len(failedBuild) + len(setupFailed), "exitstatus": pytestStatus,
		"setup_failed_packages": len(setupFailed),
		"collection_error_files": errorFiles, "collection_error_messages": messages})
	os.Exit(status)
}
