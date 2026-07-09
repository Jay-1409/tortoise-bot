static int[] twoSum(int[] nums, int target) {
    Map<Integer, Integer> seen = new HashMap<>();

    for (int i = 0; i < nums.length; i++) {
        int needed = target - nums[i];
        if (seen.containsKey(needed)) {
            return new int[] {seen.get(needed), i};
        }
        seen.put(nums[i], i);
    }

    return new int[0];
}
