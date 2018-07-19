#!/bin/bash

# THIS SCRIPT STARTS TELEMETRY ALERTS PROCEDURES; IT SHOULD BE RUN DAILY

pushd . > /dev/null
cd "$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

ln /dev/null /dev/raw1394 # this is needed to fix the `libdc1394 error: Failed to initialize libdc1394` error from OpenCV, in alert/alert.py

rm -rf ./histograms Histograms.json Scalars.yaml &&

wget https://hg.mozilla.org/mozilla-central/raw-file/tip/toolkit/components/telemetry/Histograms.json -O Histograms.json && # update histogram metadata
wget https://hg.mozilla.org/mozilla-central/raw-file/tip/toolkit/components/telemetry/Scalars.yaml -O Scalars.yaml && # update scalars metadata

nodejs exporter/export.js && # export histogram evolutions using Telemetry.js to JSON, under `histograms/*.JSON`
python alert/alert.py && # perform regression detection and output all found regressions to `dashboard/regressions.json`
python alert/post.py && # post all the found regressions above to Medusa, the Telemetry alert system
python alert/expiring.py email # detect expiring/expired histograms and alert the associated people via email

popd > /dev/null
