# Python patch release pinned: the unversioned 3.13-bookworm tag moves
FROM python:3.13.16-bookworm
ENV IS_DOCKER=True

# add default packages; versions come from constraints.txt (also applied to the build
# isolation of `pip install .` below via PIP_CONSTRAINT)
COPY requirements.txt constraints.txt /
ENV PIP_CONSTRAINT=/constraints.txt
RUN pip3 install -r /requirements.txt

# local hack to avoid auth requirements by using the public Coinbase Exchange feed
# (src/coinbase.py is ported to cryptofeed 3.0.1; refuse to override a different version)
ARG CRYPTOFEED_HACK_VERSION=3.0.1  # must equal the cryptofeed pin in constraints.txt
RUN python -c "import importlib.metadata as m, sys; v = m.version('cryptofeed'); sys.exit(0 if v == '${CRYPTOFEED_HACK_VERSION}' else 'src/coinbase.py targets cryptofeed ${CRYPTOFEED_HACK_VERSION} but ' + v + ' is installed')"
COPY src/coinbase.py /usr/local/lib/python3.13/site-packages/cryptofeed/exchanges/coinbase.py

# copy latest project changes
COPY setup.py /cryptofeed/setup.py
COPY src/ /cryptofeed/src

WORKDIR /cryptofeed
RUN pip3 install .

RUN chmod +x /cryptofeed/src/script/cryptofeed_0_startup.sh
CMD ["/cryptofeed/src/script/cryptofeed_0_startup.sh"]
