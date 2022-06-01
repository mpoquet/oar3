.. _dev-install:

Development methodology
=======================

Overview
--------

This guide explain hox to install and run locally oar3 to develop new features or fix bugs.

Installation
------------

.. note::

  This guide details how to install and run oar3 for developers.
  If you want to install oar3 on a cluster, please follow :ref:`the administration installation guide<admin-install>`.

There are currently 2 methods to install OAR:

  - from source with :ref:`nix <nix-install>`.
  - from sources with :ref:`poetry <poetry-install>`.

Alternatively, OAR can by evaluated through 2 ways:
  - oar-docker-compose `oar-docker-compose <https://github.com/oar-team/oar-docker-compose>`_
  - oar-docker (unmaintained for oar3)

Many OAR data are stored and archived in a **PostgreSQL** database.

.. _poetry-install:

Install from source with poetry
-------------------------------

*Dependencies*

  - `poetry <https://python-poetry.org/docs/#installation>`_
  - `postgresql` and `postgresql-client`, and `libpq` (headers file to link c programs)
  - The sources of `oar3 <https://github.com/oar-team/oar3>`_

Once you have all the requirements installed, you can run the commands::

  poetry install # install the python dependencies
  poetry shell # enter a shell
  pytest tests # start the tests


.. _nix-install:

Install from source with nix
----------------------------

*Dependencies*

  - `nix <https://nixos.org/download.html>`_
  - The sources of `oar3 <https://github.com/oar-team/oar3>`


Once you have all the requirements installed, you can run the commands::

  nix develop # enter a shell with oar dependencies
  pytest tests # run the tests
