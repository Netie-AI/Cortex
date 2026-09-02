"""web_search parses DuckDuckGo lite when /html is an empty shell."""

from CortexOS.execution.web_tools import parse_lite_results


def test_parse_lite_results_unwraps_uddg():
    html = """
    <a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fgithub.com%2FFoundationAgents%2FOpenManus"
       class='result-link'>OpenManus - GitHub</a>
    <td class='result-snippet'>Enjoy your own agent with OpenManus</td>
    """
    rows = parse_lite_results(html, max_results=3)
    assert len(rows) == 1
    assert rows[0]["title"] == "OpenManus - GitHub"
    assert rows[0]["url"] == "https://github.com/FoundationAgents/OpenManus"
    assert "OpenManus" in rows[0]["snippet"]
