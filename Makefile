# NetOps EVPN lab. Run `make help` for targets.
SHELL       := /bin/bash
VENV        ?= .venv
PY          := $(VENV)/bin/python
ANSIBLE     := $(VENV)/bin/ansible-playbook
PYTEST      := $(VENV)/bin/pytest
CLAB        ?= sudo containerlab
TOPOLOGY    := build/topology.clab.yml
LAB_NAME    := $(shell awk '/^lab_name:/ {print $$2}' inventory/fabric.yml)

# ansible-playbook refuses non-blocking stdin in some CI shells; feed /dev/null.
ANSIBLE_RUN := ANSIBLE_CONFIG=ansible.cfg $(ANSIBLE) </dev/null

.PHONY: help venv lint render lab deploy test destroy clean

help:
	@echo "venv     create .venv with ansible-core, pytest, yamllint"
	@echo "lint     yamllint + ansible-playbook --syntax-check"
	@echo "render   render topology, FRR and Linux configs into build/"
	@echo "deploy   render, start containerlab (if needed), push configs"
	@echo "test     run pytest against the running lab"
	@echo "destroy  stop the lab and remove containerlab state"
	@echo "clean    destroy + remove build/"

$(VENV)/bin/activate: requirements.txt
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install -q --upgrade pip
	$(VENV)/bin/pip install -q -r requirements.txt
	touch $@

venv: $(VENV)/bin/activate

lint: venv
	$(VENV)/bin/yamllint -s .
	$(ANSIBLE_RUN) --syntax-check playbooks/render.yml playbooks/deploy.yml

render: venv
	$(ANSIBLE_RUN) playbooks/render.yml

# Containerlab is (re)deployed only when the rendered topology changed or the
# lab is not running; VLAN/VNI changes alone are pushed without a redeploy.
lab: render
	@if [ ! -f build/.clab-deployed ] \
	    || ! cmp -s $(TOPOLOGY) build/.clab-deployed \
	    || [ -z "$$(docker ps -q -f label=containerlab=$(LAB_NAME))" ]; then \
	  echo "deploying containerlab topology"; \
	  $(CLAB) deploy -t $(TOPOLOGY) --reconfigure && cp $(TOPOLOGY) build/.clab-deployed; \
	else \
	  echo "containerlab topology unchanged, lab running"; \
	fi

deploy: lab
	$(ANSIBLE_RUN) playbooks/deploy.yml

test: venv
	$(PYTEST) -v tests

destroy:
	@if [ -f $(TOPOLOGY) ]; then $(CLAB) destroy -t $(TOPOLOGY) --cleanup; \
	else $(CLAB) destroy --name $(LAB_NAME) --cleanup || true; fi
	rm -f build/.clab-deployed

clean: destroy
	rm -rf build
