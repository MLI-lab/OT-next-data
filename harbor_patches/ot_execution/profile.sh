# Installed as /etc/profile.d/zzzz-ot-verifier-execution.sh for one verifier call.
# Login shells (MiMo's `bash -lc`) reset PATH in /etc/profile, and MiMo's own
# profile.d/zz-autoinstall-oom.sh replaces NODE_OPTIONS; put the execution-evidence
# shims back, only while a verifier call is active.
if [ -n "$OT_VERIFIER_EXECUTION_TOKEN" ] && [ -n "$OT_VERIFIER_EXECUTION_BIN" ] && [ -d "$OT_VERIFIER_EXECUTION_BIN" ]; then
    case ":$PATH:" in
        *":$OT_VERIFIER_EXECUTION_BIN:"*) ;;
        *) PATH=$OT_VERIFIER_EXECUTION_BIN:$PATH; export PATH ;;
    esac
    ot_node_preload=${OT_VERIFIER_EXECUTION_BIN%/bin}/ot_node_execution.js
    case " ${NODE_OPTIONS:-} " in
        *" --require $ot_node_preload "*) ;;
        *) NODE_OPTIONS="--require $ot_node_preload${NODE_OPTIONS:+ $NODE_OPTIONS}"; export NODE_OPTIONS ;;
    esac
    unset ot_node_preload
fi
# Set by the runtime on clusters whose nodes reach the internet only over IPv6
# (bridge_worker.configure_explicit_host_network); keeps the image's own options.
if [ "${OT_JAVA_PREFER_IPV6:-}" = 1 ]; then
    case " ${JAVA_TOOL_OPTIONS:-} " in
        *" -Djava.net.preferIPv6Addresses="*) ;;
        *) JAVA_TOOL_OPTIONS="${JAVA_TOOL_OPTIONS:+$JAVA_TOOL_OPTIONS }-Djava.net.preferIPv6Addresses=true"; export JAVA_TOOL_OPTIONS ;;
    esac
fi
