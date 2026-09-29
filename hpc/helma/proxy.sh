# NHR compute nodes require this proxy for outbound HTTP(S).
# https://doc.nhr.fau.de/faq/ (internet access from compute nodes)
export http_proxy=http://proxy.nhr.fau.de:80
export https_proxy=http://proxy.nhr.fau.de:80
export HTTP_PROXY="$http_proxy" HTTPS_PROXY="$https_proxy"
export no_proxy="${no_proxy:-localhost,127.0.0.1,::1}"
export NO_PROXY="$no_proxy"
