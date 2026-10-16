#!/bin/sh
exec env PYTHONPATH=${SOURCE_ROOT} ${PYTHON} -B -m poise hook-work --settings ${SETTINGS_PATH} --binding ${BINDING_PATH}
