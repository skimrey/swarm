#!/usr/bin/env python3
"""Validates `gate_repeated_widget_query_selector` against the exact live
failure (two of seven independently generated star-rating survey pages
wired their listener with a singular `document.querySelector` on a class
the same script renders once per food item), the negative controls it
must leave alone, and by sabotage.
"""
import sys

import codegen

FAILED = []


def check(label, cond, detail=None):
    status = "ok  " if cond else "FAIL"
    print("  %s %s" % (status, label))
    if not cond:
        FAILED.append(label)
        if detail is not None:
            print("      %r" % (detail,))


# The exact live shape from breakfast_survey_hover_batch_test/run_000:
# `.stars` is built once per food inside a forEach template, correctly
# queried per-instance elsewhere with an interpolated selector, and then
# wired up with a bare singular querySelector in initialize().
LIVE_BUG = r"""
const foodNames = ["Pancakes", "Waffles", "Omelets", "Bacon", "Toast"];
function buildSurvey() {
    let html = '';
    foodNames.forEach(foodName => {
        html += `
            <div class="rating-axis" data-food-name="${foodName}">
                <div class="stars" data-food-name="${foodName}">
                    ${Array(5).fill(0).map((_, i) => `
                        <span class="star" data-rating="${i + 1}"></span>
                    `).join('')}
                </div>
            </div>
        `;
    });
    formElement.innerHTML = html;
}

function restoreFromDraft(prepopulate) {
    foodNames.forEach(foodName => {
        const starsContainer = document.querySelector(`.stars[data-food-name="${foodName}"]`);
    });
}

function initialize() {
    buildSurvey();
    document.querySelector('.stars').addEventListener('mouseover', handleStarInteraction);
    document.querySelector('.stars').addEventListener('mouseout', handleStarInteraction);
    document.querySelector('.stars').addEventListener('click', handleStarInteraction);
}
"""

# The exact live shape from breakfast_survey_hover_batch_test/run_001:
# querySelector result assigned to a variable first, listener attached to
# the variable a few lines later rather than chained directly.
LIVE_BUG_VIA_VARIABLE = r"""
const foods = ["A", "B", "C"];
function build() {
    let html = '';
    foods.forEach(food => {
        html += `<div class="rating-group" data-food="${food}"></div>`;
    });
    container.innerHTML = html;
}

function initialize() {
    build();
    const starsContainer = document.querySelector('.rating-group');
    starsContainer.addEventListener('click', handleStarClick);
}
"""

# The correct pattern: querySelectorAll + forEach wires every instance.
CORRECT_QUERY_ALL = r"""
const foods = ["A", "B", "C"];
function build() {
    foods.forEach(food => {
        html += `<div class="stars" data-food="${food}"></div>`;
    });
}
function initialize() {
    build();
    document.querySelectorAll('.stars').forEach(starsContainer => {
        starsContainer.addEventListener('click', handleStarClick);
    });
}
"""

# A single, genuinely non-repeated element: the class never appears inside
# a forEach/map template anywhere, so querySelector on it is correct.
SINGLE_INSTANCE_NO_LOOP = r"""
function initialize() {
    document.querySelector('.submit-panel').addEventListener('click', onClick);
}
"""

# A per-instance query using template-literal interpolation must never be
# flagged -- it deliberately targets one specific rendered element.
INTERPOLATED_SELECTOR_IS_FINE = r"""
const foods = ["A", "B"];
function build() {
    foods.forEach(food => {
        html += `<div class="stars" data-food="${food}"></div>`;
    });
}
function wireOne(food) {
    document.querySelector(`.stars[data-food="${food}"]`).addEventListener('click', h);
}
"""

# querySelectorAll used correctly elsewhere in the same file, with an
# unrelated singular querySelector on a genuinely one-off element, must
# not be confused with the bug.
MIXED_CORRECT_USAGE = r"""
const items = ["x", "y"];
function build() {
    items.forEach(item => {
        html += `<li class="item-row" data-item="${item}"></li>`;
    });
}
function initialize() {
    document.querySelectorAll('.item-row').forEach(el => el.addEventListener('click', onRow));
    document.querySelector('.global-reset-button').addEventListener('click', onReset);
}
"""


def test_catches_the_live_chained_bug():
    failure = codegen.gate_repeated_widget_query_selector(LIVE_BUG)
    check("the live chained querySelector().addEventListener() bug is caught",
         failure is not None, failure)
    check("the message names the repeated class",
         failure and ".stars" in failure, failure)


def test_catches_the_live_via_variable_bug():
    failure = codegen.gate_repeated_widget_query_selector(LIVE_BUG_VIA_VARIABLE)
    check("the live assign-then-addEventListener bug is caught",
         failure is not None, failure)
    check("the message names the repeated class",
         failure and ".rating-group" in failure, failure)


def test_correct_query_all_pattern_not_flagged():
    check("querySelectorAll + forEach is never flagged",
         codegen.gate_repeated_widget_query_selector(CORRECT_QUERY_ALL) is None)


def test_single_instance_no_loop_not_flagged():
    check("a class never built inside a forEach/map template is left alone",
         codegen.gate_repeated_widget_query_selector(SINGLE_INSTANCE_NO_LOOP)
         is None)


def test_interpolated_selector_not_flagged():
    check("a per-instance interpolated selector is never flagged",
         codegen.gate_repeated_widget_query_selector(
             INTERPOLATED_SELECTOR_IS_FINE) is None)


def test_mixed_usage_only_flags_nothing_when_correct():
    check("correct querySelectorAll usage alongside an unrelated singular "
         "query on a real one-off element raises nothing",
         codegen.gate_repeated_widget_query_selector(MIXED_CORRECT_USAGE)
         is None)


def test_wired_into_gate_syntax_for_html_files():
    html_doc = ("<!DOCTYPE html><html><body><div id=\"x\"></div>"
               "<script>" + LIVE_BUG + "</script></body></html>")
    failure = codegen.gate_syntax(html_doc, "survey.html")
    check("gate_syntax on a real .html file surfaces the same finding",
         failure is not None and ".stars" in failure, failure)


def test_sabotage_disabling_the_loop_detector_loses_the_live_case():
    # Prove the fixture is real: without evidence the class is built
    # inside a forEach/map template, the detector has nothing to compare
    # the querySelector call against and must stay silent -- reproducing
    # the pre-fix state where this bug was invisible to every gate.
    old = codegen._LOOP_TEMPLATE_START_RE
    import re
    codegen._LOOP_TEMPLATE_START_RE = re.compile(r"$NEVER_MATCHES^")
    try:
        check("sabotage: with no loop-template signal, the live bug is missed",
             codegen.gate_repeated_widget_query_selector(LIVE_BUG) is None)
    finally:
        codegen._LOOP_TEMPLATE_START_RE = old


def main():
    test_catches_the_live_chained_bug()
    test_catches_the_live_via_variable_bug()
    test_correct_query_all_pattern_not_flagged()
    test_single_instance_no_loop_not_flagged()
    test_interpolated_selector_not_flagged()
    test_mixed_usage_only_flags_nothing_when_correct()
    test_wired_into_gate_syntax_for_html_files()
    test_sabotage_disabling_the_loop_detector_loses_the_live_case()
    print("%d failed" % len(FAILED))
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
