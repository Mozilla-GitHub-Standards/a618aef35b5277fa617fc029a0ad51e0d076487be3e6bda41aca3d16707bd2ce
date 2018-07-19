# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# Search for regression in a histogram dump directory produced by the
# node exporter.

import matplotlib.pyplot as plt

import json
import numpy
import cv2
import sys
import os
import logging
import json
import pylab
import os.path
import argparse

from time import mktime, strptime
from datetime import datetime, timedelta

histograms = None
args = None

OUTPUT_PLOTS, PLOT_FILENAME = False, "plot-{histogram_name}-{date}.png" # Whether to plot the found regressions, and what filename to save them with if plotting
REGRESSION_FILENAME = "dashboard/regressions.json"               # Path of JSON file containing a list of all found regressions
HISTOGRAM_DB = "Histograms.json"                                 # Path to JSON file containing histogram definitions map

def has_not_enough_data(hist):
    return numpy.sum(hist) < 1000 or numpy.max(hist) < 1000

def normalize(hist):
    """Returns a copy of the given histogram scaled such that its sum is 1, or 0 if this is not possible"""
    hist = hist.astype('float32')
    total = numpy.sum(hist)
    if total == 0: return hist
    return hist / total

def bat_distance(hist, ref):
    """Compute the Bhattacharyya distance between two distributions, using OpenCV"""
    return cv2.compareHist(hist, ref, 3)

def compare_range(series, idx, range, nr_ref_days):
    """Compare histogram at index `idx` to all of the histograms at indices in `range` in `series`."""
    assert iter(series) and all(len(item) == 2 for item in series), "`series` must be of the form `[(DATETIME_1, HISTOGRAM_1), ..., (DATETIME_N, HISTOGRAM_N)]`"
    assert 0 <= idx < len(series) and idx % 1 == 0, "`index` must be a valid index"
    assert iter(range) and all(0 <= i < len(series) and i % 1 == 0 for i in range), "`range` must be an iterable of valid indices"
    dt, hist = series[idx]
    hist = normalize(hist)
    distances = []
    logging.debug("Comparing " + dt.strftime("%d/%m/%Y"))

    for jdx in range:
        ref_dt, ref_hist = series[jdx]
        logging.debug("To " + ref_dt.strftime("%d/%m/%Y"))

        if has_not_enough_data(ref_hist):
            logging.debug("Reference histogram has not enough data")
            ref_hist = normalize(ref_hist)
            continue
        ref_hist = normalize(ref_hist)

        distances.append(bat_distance(hist, ref_hist))

    # There are histograms that have enough data to be compared
    if len(distances):
        logging.debug('Bhattacharyya distance: ' + str(distances[-1]))
        logging.debug('Standard deviation of the distances: ' + str(numpy.std(distances)))

    # The last compared histograms are significantly different, and the differences have a very narrow spread
    if len(distances) > nr_ref_days/2 and distances[-1] > 0.12 and numpy.std(distances) <= 0.01:
        logging.debug("Suspicious difference found")
        return (hist, ref_hist) # Produce the last compared histogram pair
    else:
        logging.debug("No suspicious difference found")
        return (None, None)

def get_raw_histograms(comparisons):
    hist = None
    ref_hist = None

    for h, r in comparisons:
        if h is not None:
            return (h, r)

    assert(False)

def compare_histogram(series, histogram, buckets, nr_ref_days = 7, nr_future_days = 2):
    """Compare the past `nr_future_days` days worth of histograms in `series` to the past `nr_ref_days` days worth of histograms in `series`, returning a list of found regressions."""
    regressions = []
    series_items = sorted(series.items(), key=lambda x: x[0]) # sorted list of pairs, each of the form (DATETIME, HISTOGRAM_FOR_THAT_DATE)

    for i, entry in enumerate(series_items[:-nr_future_days if nr_future_days else None]):
        dt, hist = entry

        logging.debug("======================")
        logging.debug("Analyzing " + dt.strftime("%d/%m/%Y"))

        if has_not_enough_data(hist): # Histogram doesn't have enough submissions to get a meaningful result
            logging.debug("Histogram has not enough data")
            continue

        comparisons = []
        ref_range = range(max(i - nr_ref_days, 0), i)

        for j in range(i, min(i + nr_future_days + 1, len(series_items))):
            comparisons.append(compare_range(series_items, j, ref_range, nr_ref_days))

        if len(comparisons) == sum(map(lambda x: x != (None, None), comparisons)):
            logging.debug('Regression found for '+ histogram + dt.strftime(", %d/%m/%Y"))
            regressions.append((dt, histogram, buckets, get_raw_histograms(comparisons)))
            if OUTPUT_PLOTS and len(buckets) < 300: # There are histograms with several hundred buckets that cause plotting to fail since the resulting image is just too large
                file_name = PLOT_FILENAME.format(histogram_name=histogram, date=dt.strftime("%d-%m-%Y"))
                plot(file_name, histogram, buckets, get_raw_histograms(comparisons))
    return regressions

def process_file(filename):
    logging.debug("Processing " + filename)
    series = {}
    buckets = []

    regressions = []
    with open(filename) as f: # one of the JSON files of the form `histograms/MEASURE_NAME.json`
        measures = json.load(f)
        for measure in measures: # a measure in this context is a histogram and a date that the histogram applies to
            # determine the date of the entry
            assert "date" in measure, "Missing date in measure"
            conv = strptime(measure['date'][:10], "%Y-%m-%d")
            measure_date = datetime.fromtimestamp(mktime(conv))

            # add the histogram values to the corresponding entry in the time series
            if measure_date in series:
                if series[measure_date].shape != numpy.array(measure["values"]).shape:
                    print "Shape mismatch in {}: {} cannot be added to {}".format(filename, series[measure_date], measure["values"])
                    continue
                series[measure_date] += numpy.array(measure["values"])
            else:
                series[measure_date] = numpy.array(measure["values"])
            buckets = measure['buckets']

        measure_name, _ = os.path.splitext(os.path.basename(filename)) # the measure name is the filename without the extension

    if series:
        # check that the series is valid
        reference_date, expected_bucket_count = None, None
        for date, histogram in series.items():
            if expected_bucket_count is None:
                reference_date, expected_bucket_count = date, len(histogram)
            elif len(histogram) != expected_bucket_count:
                logging.warn(
                    "BUCKET COUNT MISMATCH - IGNORING HISTOGRAM {} ({} has {} buckets, while {} has {} buckets)".format(
                        measure_name, reference_date, expected_bucket_count, date, len(histogram)
                    )
                )
                return []

    regressions += compare_histogram(series, measure_name, buckets)
    return regressions

def plot(file_name, histogram_name, buckets, raw_histograms):
    hist, ref_hist = raw_histograms

    fig = pylab.figure(figsize=(len(buckets)/3, 10))
    pylab.plot(hist, label="Regression", color="red")
    pylab.plot(ref_hist, label="Reference", color="blue")
    pylab.legend(shadow=True)
    pylab.title(histogram_name)
    pylab.xlabel("Bin")
    pylab.ylabel("Normalized Weight")

    pylab.xticks(range(0, len(buckets)))
    locs, labels = pylab.xticks()
    pylab.xticks(locs, buckets, rotation="45")

    pylab.savefig(file_name, bbox_inches='tight')
    pylab.close(fig)

def main():
    regressions = []

    # This is the same Histograms.json as the one in mozilla-central
    # It should always be the latest possible version when running
    with open("Histograms.json") as f:
        histograms = json.load(f)

    with open("Scalars.json") as f:
        scalars = json.load(f)

    probes = dict(histograms.items() + scalars.items())

    #logging.basicConfig(level=logging.DEBUG)
    #process_file('./histograms/FX_TAB_ANIM_ANY_FRAME_INTERVAL_MS.json', regressions)

    # Process all histograms, detecting and collecting all the regressions found
    for subdir, dirs, files in os.walk('./histograms'):
        for file in files:
            if file.endswith(".json"):
                regressions += process_file(subdir + "/" + file)

    # Load past regressions
    past_regressions = {}
    try:
        with open(REGRESSION_FILENAME) as f:
            past_regressions = json.load(f)
    except:
        pass

    # Print new regressions
    for regression in sorted(regressions, key=lambda x: x[0]):
        regression_date, histogram, buckets, raw_histograms = regression
        regression_timestamp = regression_date.isoformat()[:10]

        if regression_timestamp in past_regressions and histogram in past_regressions[regression_timestamp]:
            print 'Regression found for '+ histogram + ", " + regression_timestamp
        else:
            print 'Regression found for '+ histogram + ", " + regression_timestamp + " [new]"

            if not regression_timestamp in past_regressions:
                past_regressions[regression_timestamp] = {}

            descriptor = past_regressions[regression_timestamp][histogram] = {}
            descriptor["buckets"] = buckets
            descriptor["regression"] = raw_histograms[0].tolist()
            descriptor["reference"] = raw_histograms[1].tolist()

            name = histogram
            if histogram.startswith("STARTUP"):
                name = histogram[8:]

            probe_def = probes.get(name, {})
            descriptor["description"] = probe_def.get("description", "")
            descriptor["alert_emails"] = probe_def.get("alert_emails", probe_def.get("notification_emails", ""))

    # Store regressions found
    with open(REGRESSION_FILENAME, 'w') as f:
        json.dump(past_regressions ,f, indent=4)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Telemetry Regression Detector",
                                    formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    args = parser.parse_args()

    main()
