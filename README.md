# financial_simulator
Asking ChatGPT 4 to write me a Python personal financial simulator and optimizator.

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
