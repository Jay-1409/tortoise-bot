#include <bits/stdc++.h>
using namespace std;

// The bot replaces this marker with the submitted function implementation.
{{SOLUTION}}

int main() {
    int n;
    cin >> n;

    vector<int> nums(n);
    for (int i = 0; i < n; i++) {
        cin >> nums[i];
    }

    int target;
    cin >> target;

    vector<int> answer = two_sum(nums, target);
    sort(answer.begin(), answer.end());
    cout << answer[0] << ' ' << answer[1] << '\n';
    return 0;
}
