# financial_simulator
Asking ChatGPT 4 to write me a Python personal financial simulator and optimizator.

## Codex / 5.6 Luna note

This project was extended and tested with Codex (5.6 Luna). The simulator and
Streamlit interface are exploratory software: the outputs are illustrative,
the assumptions are configurable, and nothing here is financial advice.

Done for fun, personal, use, checking how many bugs ChatGPT 4 was able to introduce and I could discover/correct with him.  

Licensed as Attribution‑NonCommercial‑ShareAlike 2.0 Generic (CC BY-NC-SA 2.0).

The content and code provided in this context were produced outside of my professional work, during my personal free time, and are not affiliated with, nor influenced by, any of my current or previous employers. The materials and information shared are for illustrative purposes only and should not be construed as financial advice. Always consult with a financial professional before making any financial decisions. I do not accept any responsibility or liability for the accuracy, content, completeness, legality, or reliability of the information provided here. Use this material and any resultant outcomes at your own risk.


* Disclaimer: Not Financial Advice * 

Please be advised that the content presented in the previous article, code snippets, and any related materials do not constitute financial advice. I am not a licensed financial advisor. The information and tools are provided merely as examples and should not be construed as direct, personal, or professional financial guidance.

Use at Your Own Risk:

Utilize any code, results, or information shared strictly at your own peril. While attempts have been made to provide accurate and reliable information, I disclaim all liability for any unintended errors or omissions. Furthermore, I do not assume responsibility for any outcomes or impacts, financial or otherwise, that may arise from the use of the provided data or code.

Always Consult a Professional:

Financial decision-making requires a robust understanding and careful analysis of your personal financial situation. It is imperative that any actions or decisions taken based on the information shared be thoroughly vetted and validated by professional financial consultants or advisors who can accurately assess your individual needs and circumstances.

Purpose and Limitation:

The intention behind sharing this information is purely educational and to ignite curiosity and discussion within the realm of personal finance management and technology. It is essential to approach the materials with a critical mind and to understand that real-world financial decision-making encompasses a wide array of factors that may not be encapsulated within the provided examples.

Your Finances, Your Responsibility:

Remember, your financial journey is deeply personal and uniquely tailored to your specific situation. Ensure that you navigate through your financial path with utmost caution, armed with verified information from trusted professional sources, safeguarding your economic stability now and into the future.

## Running a Simulation in Google Colab

The project can be explored directly from a fresh [Google Colab](https://colab.research.google.com/) notebook:

1. Create a new notebook.
2. Clone the repository and move into it:
   ```bash
   !git clone https://github.com/filippo-mazza/financial_simulator.git
   %cd financial_simulator
   ```
3. Import the main classes and utilities:
   ```python
   from bank_account import BankAccount
   from investment import Investment
   from loan import Loan
   from income_source import IncomeSource
   from expense import Expense
   from utils import compute_net_worth_over_time
   from plots import plot_net_worth, plot_with_confidence_intervals
   ```
4. Define your accounts, income, expenses, loans and investments (see `example.py` for a template).
5. Call `compute_net_worth_over_time` with your objects to simulate the evolution of your finances.
6. Optionally visualize the results using `plot_net_worth` or `plot_with_confidence_intervals`.

This approach lets you experiment with the simulator without installing anything locally.

## Running locally

Create a virtual environment and install the development dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest
```

For repeatable simulations and tests, pass a seed to
`compute_net_worth_over_time(..., seed=42)`. The simulator restores the random
state after a seeded run, so it does not affect unrelated random operations.

## Streamlit UI

Run the interactive simulator locally:

```bash
make install
make run
```

The sidebar exposes the simulator's supported income, expense, investment, and
loan options. Use `make test` to run the unit and end-to-end test suite.

After a successful simulation, use **Export parameters and results** to download
a JSON record containing the complete scenario configuration and seed, summary
figures, month-by-month net worth/cash/investments, and loan repayment timing.
To revisit it later, open **Import saved scenario** in the sidebar, select that
JSON file, and choose **Import parameters**. The saved inputs are restored and
the simulation is rerun with the same seed.

### Screenshots

Choose a run count and click **Run simulations** to average multiple stochastic
projections; the random seeds are managed internally rather than exposed in the
UI. The dashboard combines the average summary metrics with an interactive
Vega-Lite chart. Its explicit color legends distinguish average net worth,
cash, investments, and event types. Hover for exact monthly values, or pan and
zoom to explore the projection.
Major events—including loan repayments and the configured start of school for
each dependent—appear as labeled vertical dashed lines. The standalone Python
Matplotlib helpers in `plots.py` remain available for notebook and script use.

![Streamlit dashboard](docs/screenshots/streamlit-dashboard.png)

The sidebar supports zero, one, or many incomes, expenses, investments, and
loans, plus configurable family members and age-based assumptions:

![Streamlit controls](docs/screenshots/streamlit-controls.png)

## Verification status

The project has been tested end to end in Codex (5.6 Luna). The current test
suite includes unit tests, collection edge cases, family scenarios, and a
subprocess smoke test that runs the documented five-year example with the real
financial model components.

```text
28 passed
```
