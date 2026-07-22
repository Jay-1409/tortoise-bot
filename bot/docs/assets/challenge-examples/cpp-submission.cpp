vector<int> two_sum(vector<int>& nums, int target) {
    unordered_map<int, int> seen;

    for (int i = 0; i < nums.size(); i++) {
        int needed = target - nums[i];
        if (seen.count(needed)) {
            return {seen[needed], i};
        }
        seen[nums[i]] = i;
    }

    return {};
}
