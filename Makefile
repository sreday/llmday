years := $(wildcard 20*)

generate: $(addprefix static/,$(addsuffix /index.html,$(years))) static/index.html

20%/static/index.html: 20%/_build/* 20%/_templates/* 20%/_db/* 20%/Makefile 20%/metadata.*
	@echo $@
	@echo $^
	$(eval LOCATION := 20$*)
	cd ${LOCATION} && \
		make clean && \
		make generate

static/20%/index.html: 20%/static/index.html
	@echo ">>>" $@
	cp -r 20$*/static/ static/20$*

static/index.html: home/**/*
	cd home && \
		make clean && \
		make generate && \
		cp -r static/* ../static
	cp -r photos ./static
	cp -r speakers ./static

env:
	python3 -m venv env

deps:
	pip install -r _build/requirements.txt
	mkdir -p static

clean:
	rm -rf ./static/* ./20*/static/*

serve:
	python3 _build/serve.py
	open http://localhost:8080

.PHONY: env deps clean serve
