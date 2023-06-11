"""
Temporary module for the MLHC Professional Studies Class.
"""
from collections import Counter

from google.colab import widgets
import numpy as np


def visualize_notes(notes, hadm_id):
    """
    Temporary function for visualizing notes.
    """
    # When did this patient arrive (useful for getting first 48 hours)
    admittime = notes[notes.hadm_id == hadm_id].admittime.values[0]

    # Get the notes for this patient
    notes_subject = notes.loc[notes.hadm_id == hadm_id]

    # How many notes for each category?
    category_counts = Counter(notes_subject.category.values)
    category_sorted = sorted(category_counts.keys(), key=lambda t: category_counts[t], reverse=True)

    # Outer tab is for different category of notes
    outer_tab = widgets.TabBar(category_sorted, location="top")
    for category in category_sorted:
        with outer_tab.output_to(category):
            notes_cat = notes_subject.loc[notes_subject.category == category]
            titles = []
            for num, (i, row) in enumerate(notes_cat.iterrows()):
                # Format the text with additional metadata
                time_offset = (row.charttime - admittime).total_seconds() / 3600.0
                time_offset = int(time_offset) if not np.isnan(time_offset) else "n/a"

                # Only first 48 hours of data
                titles += ["%s Note #%d (%s Hours)" % (category, num, time_offset)]

            # Inner tab is for each note in a category
            inner_tab = widgets.TabBar(titles, location="start")
            for i in range(len(titles)):
                with inner_tab.output_to(titles[i]):
                    print(notes_cat.iloc[i]["text"])
