{{SOLUTION}}

const input = require('fs').readFileSync(0, 'utf8').trim().split(/\s+/).map(Number);
const n = input[0];
const nums = input.slice(1, 1 + n);
const target = input[1 + n];

const answer = twoSum(nums, target).sort((a, b) => a - b);
console.log(`${answer[0]} ${answer[1]}`);
