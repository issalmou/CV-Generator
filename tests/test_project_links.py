"""
`source_grounding.ground_project_links` — attach a repo / demo URL to the
project it belongs to, from the PROJECTS text or from PDF link annotations
the classifier could not place in a contact slot (spec §4 / §24).

Never invents a URL, never overwrites a value the model already produced.
"""

from __future__ import annotations

from services.cv.source_grounding import ground_project_links


def _p(title, github=None, demo=None):
    return {"title": title, "description": None, "technologies": [],
            "github": github, "demo": demo}


def test_links_in_text_assigned_by_title_match():
    projects = [_p("ShopGenie — AI copywriter"), _p("CarPredict — price estimator")]
    text = (
        "ShopGenie — AI copywriter\n"
        "Repo: github.com/amine/shopgenie   Demo: shopgenie.vercel.app\n"
        "CarPredict — price estimator\n"
        "github.com/amine/carpredict | https://carpredict-demo.streamlit.app\n"
    )
    out = ground_project_links(projects, text, [])
    assert out[0]["github"] == "https://github.com/amine/shopgenie"
    assert out[0]["demo"] == "https://shopgenie.vercel.app"
    assert out[1]["github"] == "https://github.com/amine/carpredict"
    assert out[1]["demo"] == "https://carpredict-demo.streamlit.app"


def test_annotation_links_assigned_when_not_in_text():
    projects = [_p("MoodTunes - music recommender")]
    extra = ["https://github.com/sara/moodtunes", "https://moodtunes.fly.dev"]
    out = ground_project_links(projects, "MoodTunes - music recommender\ncode\nlive demo", extra)
    assert out[0]["github"] == "https://github.com/sara/moodtunes"
    assert out[0]["demo"] == "https://moodtunes.fly.dev"


def test_sole_project_missing_slot_gets_the_url():
    projects = [_p("Some Internal Tool")]
    out = ground_project_links(projects, "Some Internal Tool\n- did things\n",
                               ["https://github.com/me/whatever-repo"])
    assert out[0]["github"] == "https://github.com/me/whatever-repo"


def test_bare_github_profile_is_not_a_project_link():
    projects = [_p("Portfolio")]
    out = ground_project_links(projects, "Portfolio\n- my site\n",
                               ["https://github.com/me"])
    assert out[0]["github"] is None


def test_existing_value_is_never_overwritten():
    projects = [_p("X", github="https://github.com/me/x-original")]
    out = ground_project_links(projects, "X\n", ["https://github.com/me/x-other"])
    assert out[0]["github"] == "https://github.com/me/x-original"


def test_no_projects_is_a_noop():
    assert ground_project_links([], "text", ["https://github.com/a/b"]) == []


def test_model_supplied_url_gets_a_scheme():
    projects = [_p("X", github="github.com/me/x", demo="x-demo.vercel.app")]
    out = ground_project_links(projects, "X\n", [])
    assert out[0]["github"] == "https://github.com/me/x"
    assert out[0]["demo"] == "https://x-demo.vercel.app"
